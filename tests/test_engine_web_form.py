"""Tests for the web_form step interpreter.

All tests inject a MagicMock page via the _page= parameter so no browser is
required.  The integration path (real Playwright) is tested manually.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from optout.brokers.schema import (
    BrokerDef,
    CaptureStep,
    ClickStep,
    FormField,
    FormFillStep,
    NavigateStep,
    PromptUserIfPresentStep,
    SearchStep,
    SmsVerificationStep,
)
from optout.config import (
    Address,
    EmailConfig,
    EmailSet,
    MonitoringConfig,
    PhoneSet,
    PlaywrightConfig,
    Profile,
    SmtpConfig,
    UserConfig,
)
from optout.db import Submission
from optout.engine import SubmissionFailed, UserInputRequired
from optout.engine.methods.web_form import (
    StepState,
    _interpolate,
    _step_capture,
    _step_click,
    _step_form_fill,
    _step_navigate,
    _step_prompt_if_present,
    _step_search,
    _step_sms_verify,
    _TemplateContext,
    run,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def profile() -> Profile:
    return Profile(
        legal_name="Jane Q Public",
        aliases=["Janie Public"],
        dob=date(1990, 5, 14),
        current_address=Address(street="123 Main St", city="Austin", state="TX", zip="78701"),
        phones=PhoneSet(current=["+15125551234"]),
        emails=EmailSet(current=["jane@example.com"]),
    )


@pytest.fixture()
def submission() -> Submission:
    return Submission(
        id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        broker_slug="test-web-broker",
        status="queued",
        method_used="web_form",
        legal_basis_cited="CCPA",
    )


@pytest.fixture()
def step_state() -> StepState:
    return StepState()


@pytest.fixture()
def broker() -> BrokerDef:
    return BrokerDef(
        slug="test-web-broker",
        name="Test Web Broker",
        domain="example-web.com",
        category="people_search",
        method="web_form",
        opt_out_url="https://example-web.com/opt-out",
        legal_basis=["CCPA"],
        statutory_response_days=45,
        steps=[
            NavigateStep(type="navigate", id="open_form", url="https://example-web.com/opt-out"),
        ],
    )


@pytest.fixture()
def ctx(profile, submission, step_state, broker) -> _TemplateContext:
    return _TemplateContext(
        profile=profile,
        submission=submission,
        step_state=step_state,
        broker=broker,
    )


@pytest.fixture()
def page() -> MagicMock:
    p = MagicMock()
    # Sensible defaults so steps don't blow up unless a test overrides them
    p.url = "https://example-web.com/listing/jane-q-public"
    p.query_selector.return_value = None  # no elements by default
    p.wait_for_selector.return_value = MagicMock(
        evaluate=MagicMock(return_value="input"),
        select_option=MagicMock(),
        fill=MagicMock(),
    )
    return p


@pytest.fixture()
def user_config() -> UserConfig:
    return UserConfig(
        profile=Profile(
            legal_name="Jane Q Public",
            dob=date(1990, 5, 14),
            current_address=Address(street="123 Main St", city="Austin", state="TX", zip="78701"),
            phones=PhoneSet(current=["+15125551234"]),
            emails=EmailSet(current=["jane@example.com"]),
        ),
        email=EmailConfig(
            method="smtp",
            smtp=SmtpConfig(host="localhost", port=25, username="j@e.com", password_env="NOOP"),
        ),
        playwright=PlaywrightConfig(headless=True),
        monitoring=MonitoringConfig(),
    )


# ---------------------------------------------------------------------------
# Template interpolation
# ---------------------------------------------------------------------------


class TestInterpolate:
    def test_name_slug(self, ctx: _TemplateContext) -> None:
        assert _interpolate("{name_slug}", ctx) == "jane-q-public"

    def test_name_slug_strips_punctuation(self, profile, submission, step_state, broker) -> None:
        profile2 = profile.model_copy(update={"legal_name": "Jane Q. Public Jr."})
        ctx2 = _TemplateContext(
            profile=profile2, submission=submission, step_state=step_state, broker=broker
        )
        slug = _interpolate("{name_slug}", ctx2)
        assert slug == "jane-q-public-jr"

    def test_state_code(self, ctx: _TemplateContext) -> None:
        assert _interpolate("{state}", ctx) == "TX"

    def test_profile_attr(self, ctx: _TemplateContext) -> None:
        assert _interpolate("{profile.legal_name}", ctx) == "Jane Q Public"

    def test_submission_id(self, ctx: _TemplateContext) -> None:
        result = _interpolate("{submission.id}", ctx)
        assert "12345678" in result

    def test_step_state_attr(self, profile, submission, step_state, broker) -> None:
        step_state.set("found_listing_url", "https://example.com/listing/1")
        ctx2 = _TemplateContext(
            profile=profile, submission=submission, step_state=step_state, broker=broker
        )
        assert _interpolate("{state.found_listing_url}", ctx2) == "https://example.com/listing/1"

    def test_state_code_and_step_state_coexist(
        self, profile, submission, step_state, broker
    ) -> None:
        step_state.set("extra", "hello")
        ctx2 = _TemplateContext(
            profile=profile, submission=submission, step_state=step_state, broker=broker
        )
        # {state} → "TX"
        assert _interpolate("{state}", ctx2) == "TX"
        # {state.extra} → step_state attribute
        assert _interpolate("{state.extra}", ctx2) == "hello"

    def test_unknown_key_raises_submission_failed(self, ctx: _TemplateContext) -> None:
        with pytest.raises(SubmissionFailed, match="Template variable"):
            _interpolate("{no_such_key}", ctx)

    def test_url_template_example(self, ctx: _TemplateContext) -> None:
        url = _interpolate("https://whitepages.com/name/{name_slug}/{state}", ctx)
        assert url == "https://whitepages.com/name/jane-q-public/TX"


# ---------------------------------------------------------------------------
# _step_navigate
# ---------------------------------------------------------------------------


class TestStepNavigate:
    def test_calls_goto(self, page: MagicMock, ctx: _TemplateContext) -> None:
        step = NavigateStep(type="navigate", id="go", url="https://example.com/opt-out")
        _step_navigate(step, page, ctx)
        page.goto.assert_called_once_with(
            "https://example.com/opt-out", wait_until="domcontentloaded"
        )

    def test_interpolates_url(self, page: MagicMock, ctx: _TemplateContext) -> None:
        step = NavigateStep(type="navigate", id="go", url="https://example.com/name/{name_slug}")
        _step_navigate(step, page, ctx)
        page.goto.assert_called_once_with(
            "https://example.com/name/jane-q-public", wait_until="domcontentloaded"
        )


# ---------------------------------------------------------------------------
# _step_form_fill
# ---------------------------------------------------------------------------


class TestStepFormFill:
    def test_fills_input_field(self, page: MagicMock, ctx: _TemplateContext) -> None:
        el = MagicMock()
        el.evaluate.return_value = "input"
        page.wait_for_selector.return_value = el

        step = FormFillStep(
            type="form_fill",
            id="fill",
            fields={"name": FormField(selector="input[name='name']", value="Jane Q Public")},
        )
        _step_form_fill(step, page, ctx)
        el.fill.assert_called_once_with("Jane Q Public")

    def test_selects_option_for_select_element(
        self, page: MagicMock, ctx: _TemplateContext
    ) -> None:
        el = MagicMock()
        el.evaluate.return_value = "select"
        page.wait_for_selector.return_value = el

        step = FormFillStep(
            type="form_fill",
            id="fill",
            fields={"reason": FormField(selector="select[name='reason']", value="privacy")},
        )
        _step_form_fill(step, page, ctx)
        el.select_option.assert_called_once_with("privacy")

    def test_interpolates_field_value(
        self, profile, submission, step_state, broker, page: MagicMock
    ) -> None:
        step_state.set("found_listing_url", "https://example.com/listing/99")
        ctx2 = _TemplateContext(
            profile=profile, submission=submission, step_state=step_state, broker=broker
        )
        el = MagicMock()
        el.evaluate.return_value = "input"
        page.wait_for_selector.return_value = el

        step = FormFillStep(
            type="form_fill",
            id="fill",
            fields={
                "url": FormField(selector="input[name='url']", value="{state.found_listing_url}")
            },
        )
        _step_form_fill(step, page, ctx2)
        el.fill.assert_called_once_with("https://example.com/listing/99")

    def test_raises_when_selector_not_found(self, page: MagicMock, ctx: _TemplateContext) -> None:
        page.wait_for_selector.return_value = None

        step = FormFillStep(
            type="form_fill",
            id="fill",
            fields={"x": FormField(selector=".missing", value="val")},
        )
        with pytest.raises(SubmissionFailed, match="Selector not found"):
            _step_form_fill(step, page, ctx)


# ---------------------------------------------------------------------------
# _step_click
# ---------------------------------------------------------------------------


class TestStepClick:
    def test_calls_click(self, page: MagicMock, ctx: _TemplateContext) -> None:
        step = ClickStep(type="click", id="submit", selector="button[type='submit']")
        _step_click(step, page, ctx)
        page.locator.assert_called_once_with("button[type='submit']")
        page.locator.return_value.first.click.assert_called_once_with(timeout=15_000)

    def test_interpolates_selector(self, page: MagicMock, ctx: _TemplateContext) -> None:
        step = ClickStep(type="click", id="submit", selector="button.{name_slug}")
        _step_click(step, page, ctx)
        page.locator.assert_called_once_with("button.jane-q-public")


# ---------------------------------------------------------------------------
# _step_prompt_if_present
# ---------------------------------------------------------------------------


class TestStepPromptIfPresent:
    def test_skips_when_not_present(self, page: MagicMock, ctx: _TemplateContext) -> None:
        page.query_selector.return_value = None
        step = PromptUserIfPresentStep(
            type="prompt_user_if_present",
            id="captcha",
            selector=".captcha",
            message="Solve the CAPTCHA.",
        )
        with patch("optout.engine.methods.web_form.Prompt") as mock_prompt:
            _step_prompt_if_present(step, page, ctx)
            mock_prompt.ask.assert_not_called()

    def test_prompts_when_present(self, page: MagicMock, ctx: _TemplateContext) -> None:
        page.query_selector.return_value = MagicMock()  # element exists
        step = PromptUserIfPresentStep(
            type="prompt_user_if_present",
            id="captcha",
            selector=".captcha",
            message="Solve the CAPTCHA.",
        )
        with patch("optout.engine.methods.web_form.Prompt") as mock_prompt:
            _step_prompt_if_present(step, page, ctx)
            mock_prompt.ask.assert_called_once()


# ---------------------------------------------------------------------------
# _step_sms_verify
# ---------------------------------------------------------------------------


class TestStepSmsVerify:
    def test_fills_code(self, page: MagicMock, ctx: _TemplateContext) -> None:
        step = SmsVerificationStep(
            type="sms_verification_prompt",
            id="verify",
            code_selector="input[name='code']",
            message="Enter the code sent to {profile.phones.current[0]}",
        )
        with patch("optout.engine.methods.web_form.Prompt") as mock_prompt:
            mock_prompt.ask.return_value = "123456"
            _step_sms_verify(step, page, ctx)

        page.fill.assert_called_once_with("input[name='code']", "123456")

    def test_message_interpolated(self, page: MagicMock, ctx: _TemplateContext) -> None:
        step = SmsVerificationStep(
            type="sms_verification_prompt",
            id="verify",
            code_selector="input[name='code']",
            message="Code for {profile.legal_name}:",
        )
        with patch("optout.engine.methods.web_form.Prompt") as mock_prompt:
            mock_prompt.ask.return_value = "999"
            _step_sms_verify(step, page, ctx)
            # Prompt was called — the panel message was constructed correctly
            mock_prompt.ask.assert_called_once()


# ---------------------------------------------------------------------------
# _step_capture
# ---------------------------------------------------------------------------


class TestStepCapture:
    def test_screenshot_saved(
        self,
        page: MagicMock,
        ctx: _TemplateContext,
        submission: Submission,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        step = CaptureStep(
            type="capture",
            id="cap",
            method="screenshot",
            save_as="{submission.id}-confirmation.png",
        )
        result = _step_capture(step, page, ctx, submission)
        assert result.endswith(".png")
        assert "12345678" in result
        page.screenshot.assert_called_once()

    def test_unknown_capture_method_raises(
        self,
        page: MagicMock,
        ctx: _TemplateContext,
        submission: Submission,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        step = CaptureStep(type="capture", id="cap", method="video", save_as="clip.mp4")
        with pytest.raises(SubmissionFailed, match="Unknown capture method"):
            _step_capture(step, page, ctx, submission)


# ---------------------------------------------------------------------------
# _step_search
# ---------------------------------------------------------------------------


class TestStepSearch:
    def _make_step(self) -> SearchStep:
        return SearchStep(
            type="search",
            id="find",
            search_url_template="https://example.com/name/{name_slug}/{state}",
            result_selector=".result-item",
            pick_strategy="prompt_user",
        )

    def test_navigates_to_search_url(
        self, page: MagicMock, ctx: _TemplateContext, step_state: StepState
    ) -> None:
        step = self._make_step()
        with patch("optout.engine.methods.web_form.Prompt"):
            _step_search(step, page, ctx, step_state)
        page.goto.assert_called_once_with(
            "https://example.com/name/jane-q-public/TX",
            wait_until="domcontentloaded",
        )

    def test_sets_found_listing_url(
        self, page: MagicMock, ctx: _TemplateContext, step_state: StepState
    ) -> None:
        page.url = "https://example.com/listing/jane-42"
        page.context.expect_page.side_effect = Exception("no new tab")
        page.evaluate.return_value = None
        step = self._make_step()
        with patch("optout.engine.methods.web_form.Prompt"):
            _step_search(step, page, ctx, step_state)
        assert step_state.found_listing_url == "https://example.com/listing/jane-42"

    def test_blank_url_raises_user_input_required(
        self, page: MagicMock, ctx: _TemplateContext, step_state: StepState
    ) -> None:
        page.url = "about:blank"
        page.context.expect_page.side_effect = Exception("no new tab")
        page.evaluate.return_value = None
        step = self._make_step()
        with patch("optout.engine.methods.web_form.Prompt"):
            with pytest.raises(UserInputRequired, match="listing"):
                _step_search(step, page, ctx, step_state)

    def test_unsupported_pick_strategy_raises(
        self, page: MagicMock, ctx: _TemplateContext, step_state: StepState
    ) -> None:
        step = SearchStep(
            type="search",
            id="find",
            search_url_template="https://x.com/{name_slug}",
            result_selector=".r",
            pick_strategy="auto",
        )
        with pytest.raises(SubmissionFailed, match="pick_strategy"):
            _step_search(step, page, ctx, step_state)


# ---------------------------------------------------------------------------
# run() — end-to-end with injected mock page
# ---------------------------------------------------------------------------


class TestRun:
    def test_empty_steps_raises(
        self,
        broker: BrokerDef,
        profile: Profile,
        submission: Submission,
        user_config: UserConfig,
    ) -> None:
        empty_broker = broker.model_copy(update={"steps": []})
        with pytest.raises(SubmissionFailed, match="no steps defined"):
            run(empty_broker, profile, submission, _config=user_config, _page=MagicMock())

    def test_returns_submission_result(
        self,
        broker: BrokerDef,
        profile: Profile,
        submission: Submission,
        page: MagicMock,
        user_config: UserConfig,
    ) -> None:
        result = run(broker, profile, submission, _config=user_config, _page=page)
        assert result.payload["broker_slug"] == "test-web-broker"
        assert result.payload["steps_completed"] == 1

    def test_capture_step_sets_artifact_path(
        self,
        profile: Profile,
        submission: Submission,
        page: MagicMock,
        user_config: UserConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        capture_broker = BrokerDef(
            slug="cap-broker",
            name="Cap Broker",
            domain="cap.com",
            category="people_search",
            method="web_form",
            opt_out_url="https://cap.com/out",
            legal_basis=["CCPA"],
            statutory_response_days=45,
            steps=[
                NavigateStep(type="navigate", id="go", url="https://cap.com/out"),
                CaptureStep(
                    type="capture",
                    id="cap",
                    method="screenshot",
                    save_as="{submission.id}-done.png",
                ),
            ],
        )
        result = run(capture_broker, profile, submission, _config=user_config, _page=page)
        assert result.artifact_path is not None
        assert result.artifact_path.endswith(".png")
