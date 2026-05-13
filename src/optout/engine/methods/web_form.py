"""Playwright-based web-form opt-out handler.

Architecture
------------
run() launches Chromium and walks the broker's `steps` list.  Each step type
is dispatched to a private _step_* function.  A _TemplateContext mapping is
passed to every step so it can resolve placeholder strings such as
  {name_slug}, {state}, {profile.legal_name}, {state.found_listing_url}
without any eval().

The {state} placeholder is intentionally ambiguous in the YAML schema:
  "{state}"                  → 2-letter address state code, e.g. "TX"
  "{state.found_listing_url}"→ attribute on the mutable StepState object
_StateProxy resolves both forms transparently.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt

from ...brokers.schema import (
    AssertStep,
    BrokerDef,
    CaptureStep,
    ClickStep,
    CollectInputStep,
    FormFillStep,
    NavigateStep,
    OpenInBrowserStep,
    PromptUserIfPresentStep,
    PromptUserStep,
    SearchStep,
    SmsVerificationStep,
    WaitForStep,
)
from ...config import UserConfig, load_config
from ...db import Submission, artifacts_dir
from ...logging import get_logger
from .. import SubmissionFailed, SubmissionResult, UserInputRequired

console = Console()
log = get_logger("optout.engine.web_form")

_DEFAULT_TIMEOUT_MS = 15_000


# ---------------------------------------------------------------------------
# Mutable execution state (shared across steps in one run)
# ---------------------------------------------------------------------------


class StepState:
    """Flexible dict-backed object that accumulates values between steps."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)

    def __repr__(self) -> str:
        return f"StepState({self._data!r})"


# ---------------------------------------------------------------------------
# Template interpolation
# ---------------------------------------------------------------------------


class _StateProxy:
    """
    Dual-mode proxy for the {state} placeholder:
      str({state})           → "TX"   (address state code)
      {state}.found_listing  → StepState attribute
    """

    __slots__ = ("_code", "_step_state")

    def __init__(self, state_code: str, step_state: StepState) -> None:
        self._code = state_code
        self._step_state = step_state

    def __str__(self) -> str:
        return self._code

    def __format__(self, spec: str) -> str:
        return format(self._code, spec)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._step_state, name)


_STATE_NAMES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New-Hampshire",
    "NJ": "New-Jersey",
    "NM": "New-Mexico",
    "NY": "New-York",
    "NC": "North-Carolina",
    "ND": "North-Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode-Island",
    "SC": "South-Carolina",
    "SD": "South-Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West-Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "Washington-DC",
}


class _TemplateContext(Mapping):
    """Mapping for str.format_map() that resolves broker YAML placeholders."""

    def __init__(
        self,
        *,
        profile: Any,
        submission: Submission,
        step_state: StepState,
        broker: BrokerDef,
    ) -> None:
        self._profile = profile
        self._submission = submission
        self._step_state = step_state
        self._broker = broker
        raw = getattr(profile, "legal_name", "").lower()
        self._name_slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
        self._name_title_slug = "-".join(w.capitalize() for w in self._name_slug.split("-"))
        parts = getattr(profile, "legal_name", "").split()
        self._name_first = parts[0] if parts else ""
        self._name_last = " ".join(parts[1:]) if len(parts) > 1 else ""
        dob = getattr(profile, "dob", None)
        self._age = str((date.today() - dob).days // 365) if dob else ""
        self._birth_year = str(dob.year) if dob else ""

    def __getitem__(self, key: str) -> Any:
        if key == "name_slug":
            return self._name_slug
        if key == "name_title_slug":
            return self._name_title_slug
        if key == "name_first":
            return self._name_first
        if key == "name_last":
            return self._name_last
        if key == "age":
            return self._age
        if key == "birth_year":
            return self._birth_year
        if key == "phone_current":
            phones = getattr(self._profile, "phones", None)
            current = getattr(phones, "current", []) if phones is not None else []
            return current[0] if current else ""
        if key == "state_name":
            addr = getattr(self._profile, "current_address", None)
            code = getattr(addr, "state", "") if addr is not None else ""
            full = _STATE_NAMES.get(code.upper(), code)
            return full.replace("-", " ")
        if key == "profile":
            return self._profile
        if key == "submission":
            return self._submission
        if key == "broker":
            return self._broker
        if key == "state":
            addr = getattr(self._profile, "current_address", None)
            code = getattr(addr, "state", "") if addr is not None else ""
            return _StateProxy(code, self._step_state)
        if key == "state_full":
            addr = getattr(self._profile, "current_address", None)
            code = getattr(addr, "state", "") if addr is not None else ""
            return _STATE_NAMES.get(code.upper(), code)
        raise KeyError(key)

    def __iter__(self):
        return iter(
            (
                "name_slug",
                "name_title_slug",
                "name_first",
                "name_last",
                "age",
                "birth_year",
                "phone_current",
                "state_name",
                "profile",
                "submission",
                "broker",
                "state",
                "state_full",
            )
        )

    def __len__(self) -> int:
        return 13


def _interpolate(template: str, ctx: _TemplateContext) -> str:
    """Substitute all {placeholders} in *template* using *ctx*."""
    try:
        return template.format_map(ctx)
    except (AttributeError, KeyError) as exc:
        raise SubmissionFailed(
            f"Template variable {exc} not available in {template!r}. "
            "A required earlier step may not have completed successfully."
        ) from exc


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(
    broker: BrokerDef,
    profile: Any,
    submission: Submission,
    *,
    _config: UserConfig | None = None,
    _page: Any = None,  # injectable mock for tests
) -> SubmissionResult:
    """Walk *broker*.steps using a Playwright browser page."""
    if not broker.steps:
        raise SubmissionFailed(
            f"Broker '{broker.slug}' has no steps defined — cannot run web_form handler."
        )

    config = _config or load_config()
    step_state = StepState()
    ctx = _TemplateContext(
        profile=profile,
        submission=submission,
        step_state=step_state,
        broker=broker,
    )
    artifact_path: str | None = None

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(submission_id=str(submission.id))

    if _page is not None:
        artifact_path = _run_steps(broker.steps, _page, ctx, step_state, submission)
    else:
        from ..browser import launch_browser

        with launch_browser(config.playwright) as ctx_browser:
            page = ctx_browser.new_page()
            try:
                artifact_path = _run_steps(broker.steps, page, ctx, step_state, submission)
            finally:
                page.close()

    return SubmissionResult(
        payload={
            "broker_slug": broker.slug,
            "steps_completed": len(broker.steps),
            "found_listing_url": step_state.found_listing_url,
        },
        artifact_path=artifact_path,
    )


def _run_steps(steps, page, ctx, step_state, submission) -> str | None:
    artifact_path: str | None = None
    for step in steps:
        display = step.label or step.id
        console.print(f"  [dim]→[/dim] {display}")

        t0 = time.monotonic()
        structlog.contextvars.bind_contextvars(step_id=step.id)
        log.debug("step.start", step_type=step.type, label=display)

        result = _execute_step_with_resilience(step, page, ctx, step_state, submission)

        duration_ms = round((time.monotonic() - t0) * 1000)
        log.debug("step.end", step_type=step.type, duration_ms=duration_ms)

        if result is not None:
            artifact_path = result
    return artifact_path


def _execute_step_with_resilience(step, page, ctx, step_state, submission) -> str | None:
    """Wrap _dispatch() with retry, optional, and on_failure behavior."""
    retry = step.retry
    max_attempts = retry.attempts if retry else 1
    backoff = retry.backoff_seconds if retry else 1.0

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _dispatch(step, page, ctx, step_state, submission)
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                wait = backoff * (2 ** (attempt - 1))
                log.warning(
                    "step.retry",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    wait_s=wait,
                    error=str(exc),
                )
                time.sleep(wait)

    assert last_exc is not None

    if step.optional or step.on_failure == "skip":
        log.warning("step.skipped", step_id=step.id, error=str(last_exc))
        return None

    if step.on_failure == "prompt":
        console.print(
            Panel(
                f"[bold red]Step '{escape(step.id)}' failed:[/bold red]\n"
                f"{escape(str(last_exc))}\n\nChoose how to proceed:",
                title="Step failed",
                border_style="red",
                expand=False,
            )
        )
        choice = Prompt.ask("Action", choices=["skip", "retry", "abort"], default="abort")
        if choice == "skip":
            log.warning("step.on_failure.prompt.skip", step_id=step.id)
            return None
        if choice == "retry":
            log.info("step.on_failure.prompt.retry", step_id=step.id)
            return _execute_step_with_resilience(step, page, ctx, step_state, submission)
        raise last_exc

    raise last_exc


# ---------------------------------------------------------------------------
# Step dispatcher
# ---------------------------------------------------------------------------


def _dispatch(step, page, ctx, step_state, submission) -> str | None:
    if isinstance(step, SearchStep):
        _step_search(step, page, ctx, step_state)
    elif isinstance(step, NavigateStep):
        _step_navigate(step, page, ctx)
    elif isinstance(step, FormFillStep):
        _step_form_fill(step, page, ctx)
    elif isinstance(step, PromptUserStep):
        _step_prompt_user(step, ctx)
    elif isinstance(step, PromptUserIfPresentStep):
        _step_prompt_if_present(step, page, ctx)
    elif isinstance(step, ClickStep):
        _step_click(step, page, ctx)
    elif isinstance(step, SmsVerificationStep):
        _step_sms_verify(step, page, ctx)
    elif isinstance(step, CaptureStep):
        return _step_capture(step, page, ctx, submission)
    elif isinstance(step, WaitForStep):
        _step_wait_for(step, page, ctx)
    elif isinstance(step, OpenInBrowserStep):
        _step_open_in_browser(step, ctx)
    elif isinstance(step, CollectInputStep):
        _step_collect_input(step, ctx, step_state)
    elif isinstance(step, AssertStep):
        _step_assert(step, page, ctx)
    else:
        raise SubmissionFailed(f"Unknown step type: {type(step).__name__!r}")
    return None


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def _step_search(
    step: SearchStep,
    page: Any,
    ctx: _TemplateContext,
    step_state: StepState,
) -> None:
    search_url = _interpolate(step.search_url_template, ctx)
    log.debug("step.search.navigate", url=search_url)
    page.goto(search_url, wait_until="domcontentloaded")

    # Wait for results selector to appear (may be empty)
    page.wait_for_selector(step.result_selector, timeout=_DEFAULT_TIMEOUT_MS, state="attached")

    if step.pick_strategy != "prompt_user":
        raise SubmissionFailed(
            f"Unsupported pick_strategy {step.pick_strategy!r} in step '{step.id}'. "
            "Only 'prompt_user' is implemented."
        )

    # Inject a click listener BEFORE showing the prompt.
    # When the user clicks their listing, Whitepages JS intercepts the click
    # and redirects to /checkout/.  But the <a> href attribute still holds
    # the real /name/… profile URL.  We capture it in capture phase (true)
    # so it fires before any JS handler can navigate away.
    page.evaluate("""() => {
        window._optout_profile_href = null;
        document.addEventListener('click', (e) => {
            const a = e.target.closest('a[href]');
            if (a && a.href && a.href.includes('/name/')) {
                window._optout_profile_href = a.href;
            }
        }, true);
    }""")

    console.print(
        Panel(
            "[bold]Find your listing[/bold]\n\n"
            "The browser window shows search results.\n"
            "1. Locate [bold]your[/bold] listing.\n"
            "2. Click on it (a new tab will open — that's fine, ignore it).\n"
            "3. Come back here and press [bold]Enter[/bold].\n\n"
            "[dim]The opt-out form will open automatically.[/dim]",
            title="Action required",
            border_style="yellow",
            expand=False,
        )
    )

    listing_url: str = ""
    try:
        with page.context.expect_page(timeout=5_000) as new_page_info:
            Prompt.ask("Press Enter when you are on your listing page")
        listing_page = new_page_info.value
        try:
            listing_page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        listing_url = listing_page.url
    except Exception:
        listing_url = page.url

    # Best source: the real /name/… href captured before JS redirect
    try:
        profile_href = page.evaluate("() => window._optout_profile_href")
        if profile_href and "/name/" in profile_href:
            listing_url = profile_href
            log.debug("step.search.href_captured", url=listing_url)
    except Exception:
        pass

    # Last-resort fallback: extract wpId from checkout URL
    if "/checkout/" in listing_url:
        try:
            wp_id = parse_qs(urlparse(listing_url).query).get("wpId", [None])[0]
            if wp_id:
                name_slug = str(ctx["name_title_slug"])
                state_code = str(ctx["state"])
                listing_url = f"https://www.whitepages.com/name/{name_slug}/{state_code}/{wp_id}"
                log.debug("step.search.url_rewrite", url=listing_url)
        except Exception:
            pass

    if not listing_url or listing_url in ("about:blank", search_url):
        raise UserInputRequired(
            "The browser is still on the search results page — no listing was selected. "
            "Click your listing in the browser first, then press Enter."
        )

    step_state.set("found_listing_url", listing_url)
    log.debug("step.search.listing_url", url=listing_url)


def _step_navigate(step: NavigateStep, page: Any, ctx: _TemplateContext) -> None:
    url = _interpolate(step.url, ctx)
    log.debug("step.navigate", url=url)
    page.goto(url, wait_until="domcontentloaded")


def _step_wait_for(step: WaitForStep, page: Any, ctx: _TemplateContext) -> None:
    msg = _interpolate(step.message, ctx) if step.message else None
    if msg:
        console.print(
            Panel(
                msg, title="Waiting — action may be required", border_style="yellow", expand=False
            )
        )
    if step.js_condition:
        log.debug("step.wait_for.js", timeout_ms=step.timeout_ms)
        page.wait_for_function(step.js_condition, timeout=step.timeout_ms)
        return
    selector = _interpolate(step.selector, ctx)
    log.debug("step.wait_for.selector", selector=selector, timeout_ms=step.timeout_ms)
    if step.frame:
        page.frame_locator(step.frame).locator(selector).wait_for(
            timeout=step.timeout_ms, state="attached"
        )
    else:
        element = page.wait_for_selector(selector, timeout=step.timeout_ms)
        if element is None:
            raise SubmissionFailed(
                f"Selector {selector!r} never appeared within {step.timeout_ms}ms"
            )


def _step_open_in_browser(step: OpenInBrowserStep, ctx: _TemplateContext) -> None:
    import webbrowser

    url = _interpolate(step.url, ctx)
    instructions = _interpolate(step.instructions, ctx)
    log.debug("step.open_in_browser", url=url)
    webbrowser.open(url)
    console.print(
        Panel(
            instructions,
            title="Complete in your browser",
            border_style="yellow",
            expand=False,
        )
    )
    Prompt.ask("Press Enter when you have finished all steps in your browser")


def _step_form_fill(step: FormFillStep, page: Any, ctx: _TemplateContext) -> None:
    frame_loc = page.frame_locator(step.frame) if step.frame else None
    for field_name, field in step.fields.items():
        value = _interpolate(field.value, ctx)
        if value is None or str(value) == "None":
            raise SubmissionFailed(
                f"Field '{field_name}' resolved to None — "
                f"template {field.value!r} may depend on a step that didn't complete."
            )
        log.debug("step.form_fill.field", field=field_name, selector=field.selector)

        if frame_loc is not None:
            locator = frame_loc.locator(field.selector)
            locator.wait_for(timeout=_DEFAULT_TIMEOUT_MS, state="attached")
            tag = locator.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                locator.select_option(str(value), force=True)
            else:
                locator.fill(str(value), force=True)
        else:
            wait_state = "attached" if field.force else "visible"
            element = page.wait_for_selector(
                field.selector, timeout=_DEFAULT_TIMEOUT_MS, state=wait_state
            )
            if element is None:
                raise SubmissionFailed(
                    f"Selector not found for field '{field_name}': {field.selector!r}"
                )
            tag = element.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                element.select_option(str(value))
            elif field.use_type:
                # React/Vue forms that ignore fill() need real keyboard events
                element.click()
                page.wait_for_timeout(150)
                page.keyboard.type(str(value), delay=20)
            elif field.use_js:
                # React controlled inputs reset via state on re-render — bypass React
                # by using the native HTMLInputElement value setter then firing input/change
                first_selector = field.selector.split(",")[0].strip()
                page.evaluate(
                    """([sel, val]) => {
                        const el = document.querySelector(sel);
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(el, val);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }""",
                    [first_selector, str(value)],
                )
            elif field.force:
                element.fill(str(value), force=True)
            else:
                element.fill(str(value))


def _step_prompt_user(step: PromptUserStep, ctx: _TemplateContext) -> None:
    message = _interpolate(step.message, ctx)
    console.print(Panel(message, title="Action required", border_style="yellow", expand=False))
    Prompt.ask("Press Enter when done")


def _step_prompt_if_present(
    step: PromptUserIfPresentStep, page: Any, ctx: _TemplateContext
) -> None:
    if step.frame:
        is_present = page.frame_locator(step.frame).locator(step.selector).count() > 0
    else:
        is_present = page.query_selector(step.selector) is not None

    if not is_present:
        log.debug("step.prompt_if_present.skip", selector=step.selector)
        return

    message = _interpolate(step.message, ctx)
    console.print(
        Panel(
            f"{message}",
            title="Action required",
            border_style="yellow",
            expand=False,
        )
    )
    Prompt.ask("Press Enter when done")


def _step_click(step: ClickStep, page: Any, ctx: _TemplateContext) -> None:
    selector = _interpolate(step.selector, ctx)
    log.debug("step.click", selector=selector)
    if step.frame:
        page.frame_locator(step.frame).locator(selector).click(timeout=_DEFAULT_TIMEOUT_MS)
    else:
        page.locator(selector).first.click(timeout=_DEFAULT_TIMEOUT_MS)


def _step_sms_verify(step: SmsVerificationStep, page: Any, ctx: _TemplateContext) -> None:
    message = _interpolate(step.message, ctx)
    console.print(
        Panel(
            f"{message}",
            title="SMS Verification",
            border_style="yellow",
            expand=False,
        )
    )
    code = Prompt.ask("Enter the SMS code")
    page.wait_for_selector(step.code_selector, timeout=_DEFAULT_TIMEOUT_MS)
    page.fill(step.code_selector, code)
    log.debug("step.sms_verify.done", selector=step.code_selector)


def _step_collect_input(
    step: CollectInputStep,
    ctx: _TemplateContext,
    step_state: StepState,
) -> None:
    message = _interpolate(step.message, ctx)
    console.print(Panel(message, title="Input required", border_style="yellow", expand=False))
    value = Prompt.ask("Paste here").strip()
    if not value:
        raise SubmissionFailed(f"No value entered for collect_input step '{step.id}'.")
    step_state.set(step.store_as, value)
    log.debug("step.collect_input.stored", key=step.store_as)


def _step_assert(step: AssertStep, page: Any, ctx: _TemplateContext) -> None:
    failures: list[str] = []

    if step.url_matches:
        pattern = _interpolate(step.url_matches, ctx)
        current_url = page.url
        if not re.search(pattern, current_url):
            failures.append(f"url_matches: expected pattern {pattern!r}, got {current_url!r}")

    if step.selector:
        selector = _interpolate(step.selector, ctx)
        element = page.query_selector(selector)
        if element is None:
            failures.append(f"selector: {selector!r} not found on page")
        elif step.text_contains:
            text = element.text_content() or ""
            expected = _interpolate(step.text_contains, ctx)
            if expected not in text:
                failures.append(f"text_contains: expected {expected!r} in {text!r}")
    elif step.text_contains:
        body_text = page.evaluate("() => document.body.innerText")
        expected = _interpolate(step.text_contains, ctx)
        if expected not in (body_text or ""):
            failures.append(f"text_contains: expected {expected!r} on page")

    if step.js_condition:
        result = page.evaluate(step.js_condition)
        if not result:
            failures.append(f"js_condition: {step.js_condition!r} returned falsy")

    if failures:
        raise SubmissionFailed(
            f"Assertion failed in step '{step.id}':\n" + "\n".join(f"  • {f}" for f in failures)
        )

    log.debug("step.assert.passed", step_id=step.id)


def _step_capture(
    step: CaptureStep,
    page: Any,
    ctx: _TemplateContext,
    submission: Submission,
) -> str:
    save_as = _interpolate(step.save_as, ctx)
    path = artifacts_dir() / save_as
    log.debug("step.capture", method=step.method, filename=path.name)

    if step.method == "screenshot":
        try:
            page.screenshot(path=str(path), full_page=True)
        except Exception as exc:
            console.print(
                f"[yellow]  ⚠ Screenshot skipped — page closed before capture "
                f"({exc.__class__.__name__})[/yellow]"
            )
            log.warning("step.capture.skipped", error=str(exc))
            return None
    else:
        raise SubmissionFailed(f"Unknown capture method {step.method!r} in step '{step.id}'.")

    return str(path)
