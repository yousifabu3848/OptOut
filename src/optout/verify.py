"""Broker drift detection — check whether step selectors still exist on live pages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .brokers.schema import (
    AssertStep,
    BrokerDef,
    ClickStep,
    FormFillStep,
    PromptUserIfPresentStep,
    SearchStep,
    SmsVerificationStep,
    WaitForStep,
)


@dataclass
class SelectorCheck:
    step_id: str
    step_type: str
    selector: str
    found: bool


@dataclass
class BrokerVerifyResult:
    slug: str
    name: str
    status: str  # "pass" | "warn" | "fail"
    selectors: list[SelectorCheck] = field(default_factory=list)
    error: str | None = None


def _collect_selectors(broker: BrokerDef) -> list[tuple[str, str, str]]:
    """Return (step_id, step_type, selector) for every checkable selector in the broker."""
    items: list[tuple[str, str, str]] = []
    for step in broker.steps:
        if isinstance(step, ClickStep) and step.selector:
            items.append((step.id, step.type, step.selector))
        elif isinstance(step, FormFillStep):
            for fname, fld in step.fields.items():
                items.append((f"{step.id}.{fname}", step.type, fld.selector))
        elif isinstance(step, WaitForStep) and step.selector:
            items.append((step.id, step.type, step.selector))
        elif isinstance(step, PromptUserIfPresentStep) and step.selector:
            items.append((step.id, step.type, step.selector))
        elif isinstance(step, AssertStep) and step.selector:
            items.append((step.id, step.type, step.selector))
        elif isinstance(step, SearchStep):
            items.append((step.id, step.type, step.result_selector))
        elif isinstance(step, SmsVerificationStep):
            items.append((step.id, step.type, step.code_selector))
    return items


def verify_broker(
    broker: BrokerDef,
    *,
    headless: bool = True,
    timeout_ms: int = 20_000,
) -> BrokerVerifyResult:
    """Load broker.opt_out_url in Playwright and check each step selector."""
    from playwright.sync_api import sync_playwright

    if not broker.opt_out_url:
        return BrokerVerifyResult(
            slug=broker.slug,
            name=broker.name,
            status="warn",
            error="No opt_out_url — cannot load a page to check",
        )

    selectors = _collect_selectors(broker)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            try:
                page.goto(broker.opt_out_url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception as exc:
                browser.close()
                return BrokerVerifyResult(
                    slug=broker.slug,
                    name=broker.name,
                    status="fail",
                    error=f"Page failed to load: {exc}",
                )

            checks: list[SelectorCheck] = []
            for step_id, step_type, selector in selectors:
                try:
                    found = page.query_selector(selector) is not None
                except Exception:
                    found = False
                checks.append(
                    SelectorCheck(
                        step_id=step_id, step_type=step_type, selector=selector, found=found
                    )
                )

            browser.close()

    except Exception as exc:
        return BrokerVerifyResult(
            slug=broker.slug,
            name=broker.name,
            status="fail",
            error=str(exc),
        )

    missing = [c for c in checks if not c.found]
    if not checks:
        status = "warn"  # no selectors to check
    elif missing:
        status = "warn"  # some selectors missing (may be on later pages)
    else:
        status = "pass"

    return BrokerVerifyResult(slug=broker.slug, name=broker.name, status=status, selectors=checks)


def update_last_verified_at(yaml_path: Path, today: date | None = None) -> None:
    """Rewrite the last_verified_at line in a broker YAML to today's date."""
    import re

    if today is None:
        today = date.today()

    content = yaml_path.read_text()
    new_line = f"last_verified_at: {today.isoformat()}"

    if re.search(r"^last_verified_at:", content, flags=re.MULTILINE):
        content = re.sub(r"^last_verified_at:.*$", new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip() + f"\n{new_line}\n"

    yaml_path.write_text(content)
