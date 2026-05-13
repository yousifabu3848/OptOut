"""Tests for scan/scanner.py, monitoring.py, and DB scan helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from optout.brokers.loader import load_broker
from optout.db import (
    create_submission,
    get_last_scan,
    get_session,
    init_db,
    record_scan,
)
from optout.monitoring import BrokerScanReport, MonitorSummary, _due_for_rescan, run_monitor
from optout.scan.scanner import (
    ScanOutcome,
    _name_in_text,
    _name_slug,
    _run_scan_on_page,
    scan_broker,
)

FIXTURES = Path(__file__).parent / "fixtures" / "brokers"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Isolate every test to its own SQLite file."""
    db_file = tmp_path / "optout.db"
    monkeypatch.setattr("optout.db.db_path", lambda: db_file)
    init_db()
    return db_file


@pytest.fixture
def email_broker():
    return load_broker(FIXTURES / "email_broker.yml")


@pytest.fixture
def web_broker():
    return load_broker(FIXTURES / "web_form_broker.yml")


@pytest.fixture
def sample_profile():
    from datetime import date

    from optout.config import Address, EmailSet, PhoneSet, Profile

    return Profile(
        legal_name="Jane Q Public",
        aliases=["Janie Public"],
        dob=date(1990, 5, 14),
        current_address=Address(street="123 Main St", city="Austin", state="TX", zip="78701"),
        phones=PhoneSet(current=["+15125551234"]),
        emails=EmailSet(current=["jane@example.com"]),
    )


@pytest.fixture
def minimal_config(sample_profile):
    from optout.config import (
        EmailConfig,
        MonitoringConfig,
        PlaywrightConfig,
        SmtpConfig,
        UserConfig,
    )

    return UserConfig(
        profile=sample_profile,
        email=EmailConfig(
            method="smtp",
            smtp=SmtpConfig(
                host="smtp.example.com",
                port=587,
                username="jane@example.com",
                password_env="OPTOUT_SMTP_PASSWORD",
            ),
        ),
        playwright=PlaywrightConfig(headless=True),
        monitoring=MonitoringConfig(rescan_interval_days=30),
    )


def _mock_page(
    elements: list[str],
    *,
    raise_on_wait: bool = False,
    raise_generic: bool = False,
) -> MagicMock:
    """Build a minimal Playwright page mock."""
    page = MagicMock()

    if raise_generic:
        page.goto.side_effect = RuntimeError("network error")
        return page

    if raise_on_wait:
        from playwright.sync_api import TimeoutError as PwTimeout

        page.wait_for_selector.side_effect = PwTimeout("timeout")
        return page

    mock_els = []
    for text in elements:
        el = MagicMock()
        el.inner_text.return_value = text
        el.get_attribute.return_value = None
        el.query_selector.return_value = None
        mock_els.append(el)

    page.query_selector_all.return_value = mock_els
    return page


# ---------------------------------------------------------------------------
# Unit: _name_slug
# ---------------------------------------------------------------------------


class TestNameSlug:
    def test_simple(self) -> None:
        assert _name_slug("Jane Public") == "Jane-Public"

    def test_middle_name(self) -> None:
        assert _name_slug("Jane Q Public") == "Jane-Q-Public"

    def test_single_word(self) -> None:
        assert _name_slug("Madonna") == "Madonna"


# ---------------------------------------------------------------------------
# Unit: _name_in_text
# ---------------------------------------------------------------------------


class TestNameInText:
    def test_exact_match(self) -> None:
        assert _name_in_text("Jane Public", "Jane Public, Austin TX")

    def test_case_insensitive(self) -> None:
        assert _name_in_text("Jane Public", "JANE PUBLIC Austin")

    def test_partial_name_fails(self) -> None:
        assert not _name_in_text("Jane Public", "Jane Austin TX")

    def test_alias_match(self) -> None:
        assert _name_in_text("Janie Public", "Janie Public, 30 Austin TX")

    def test_no_match(self) -> None:
        assert not _name_in_text("Jane Public", "John Smith, Dallas TX")


# ---------------------------------------------------------------------------
# Unit: _run_scan_on_page
# ---------------------------------------------------------------------------


class TestRunScanOnPage:
    def test_found_when_name_in_result(self, web_broker, sample_profile) -> None:
        page = _mock_page(["Jane Q Public, 123 Main St, Austin TX"])
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.result == "found"
        assert len(outcome.matches) == 1
        assert "Jane Q Public" in outcome.matches[0].text

    def test_clean_when_no_name_match(self, web_broker, sample_profile) -> None:
        page = _mock_page(["John Smith, 456 Oak Ave, Dallas TX"])
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.result == "clean"
        assert outcome.matches == []

    def test_alias_triggers_found(self, web_broker, sample_profile) -> None:
        page = _mock_page(["Janie Public, 123 Main St, Austin TX"])
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.result == "found"

    def test_clean_when_no_elements(self, web_broker, sample_profile) -> None:
        page = _mock_page([])
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.result == "clean"

    def test_inconclusive_on_timeout(self, web_broker, sample_profile) -> None:
        page = _mock_page([], raise_on_wait=True)
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.result == "inconclusive"
        assert outcome.error is not None

    def test_inconclusive_on_generic_error(self, web_broker, sample_profile) -> None:
        page = _mock_page([], raise_generic=True)
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.result == "inconclusive"
        assert "network error" in (outcome.error or "")

    def test_inconclusive_without_scan_config(self, email_broker, sample_profile) -> None:
        page = _mock_page(["Jane Q Public"])
        outcome = _run_scan_on_page(page, email_broker, sample_profile)
        assert outcome.result == "inconclusive"

    def test_multiple_elements_all_checked(self, web_broker, sample_profile) -> None:
        page = _mock_page(
            [
                "John Smith, Dallas TX",
                "Jane Q Public, Austin TX",
                "Bob Jones, Houston TX",
            ]
        )
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.result == "found"
        assert len(outcome.matches) == 1

    def test_text_truncated_at_300(self, web_broker, sample_profile) -> None:
        long_text = "Jane Q Public " + "x" * 400
        page = _mock_page([long_text])
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.result == "found"
        assert len(outcome.matches[0].text) <= 300

    def test_url_captured_from_link(self, web_broker, sample_profile) -> None:
        page = MagicMock()
        el = MagicMock()
        el.inner_text.return_value = "Jane Q Public, Austin TX"
        el.get_attribute.return_value = None
        link = MagicMock()
        link.get_attribute.return_value = "https://example.com/jane"
        el.query_selector.return_value = link
        page.query_selector_all.return_value = [el]
        outcome = _run_scan_on_page(page, web_broker, sample_profile)
        assert outcome.matches[0].url == "https://example.com/jane"


# ---------------------------------------------------------------------------
# Unit: scan_broker (injection path)
# ---------------------------------------------------------------------------


class TestScanBroker:
    def test_returns_outcome_with_injected_page(self, web_broker, sample_profile) -> None:
        page = _mock_page(["Jane Q Public, Austin TX"])
        outcome = scan_broker(web_broker, sample_profile, _page=page)
        assert isinstance(outcome, ScanOutcome)
        assert outcome.result == "found"

    def test_no_scan_config_returns_inconclusive(self, email_broker, sample_profile) -> None:
        # email_broker has scan: null — returns early before any Playwright import
        outcome = scan_broker(email_broker, sample_profile)
        assert outcome.result == "inconclusive"

    def test_injected_page_bypasses_playwright(
        self, web_broker, sample_profile, monkeypatch
    ) -> None:
        # Ensure sync_playwright is never reached when _page is supplied
        monkeypatch.setattr(
            "playwright.sync_api.sync_playwright",
            lambda: (_ for _ in ()).throw(AssertionError("sync_playwright should not be called")),
        )
        page = _mock_page([])
        outcome = scan_broker(web_broker, sample_profile, _page=page)
        assert outcome.result == "clean"


# ---------------------------------------------------------------------------
# DB helpers: record_scan / get_last_scan
# ---------------------------------------------------------------------------


class TestDbScanHelpers:
    def test_record_scan_persists(self, tmp_db) -> None:
        with get_session() as session:
            scan = record_scan(session, "radaris", "clean")
        assert scan.id is not None
        assert scan.broker_slug == "radaris"
        assert scan.result == "clean"

    def test_record_scan_with_matches(self, tmp_db) -> None:
        matches = json.dumps([{"text": "Jane Public, Austin TX", "url": None}])
        with get_session() as session:
            record_scan(session, "spokeo", "found", matches_json=matches)
        with get_session() as session:
            last = get_last_scan(session, "spokeo")
        assert last is not None
        assert last.result == "found"
        assert last.matches_json == matches

    def test_get_last_scan_none_when_empty(self, tmp_db) -> None:
        with get_session() as session:
            assert get_last_scan(session, "whitepages") is None

    def test_get_last_scan_returns_most_recent(self, tmp_db) -> None:
        with get_session() as session:
            record_scan(session, "radaris", "clean")
        with get_session() as session:
            record_scan(session, "radaris", "found")
        with get_session() as session:
            last = get_last_scan(session, "radaris")
        assert last is not None
        assert last.result == "found"

    def test_get_last_scan_isolated_by_slug(self, tmp_db) -> None:
        with get_session() as session:
            record_scan(session, "radaris", "found")
        with get_session() as session:
            record_scan(session, "mylife", "clean")
        with get_session() as session:
            assert get_last_scan(session, "mylife").result == "clean"
            assert get_last_scan(session, "radaris").result == "found"


# ---------------------------------------------------------------------------
# Unit: _due_for_rescan
# ---------------------------------------------------------------------------


class TestDueForRescan:
    def test_none_is_always_due(self) -> None:
        assert _due_for_rescan(None, 30) is True

    def test_recent_scan_not_due(self) -> None:
        scan = MagicMock()
        scan.scanned_at = datetime.now(UTC) - timedelta(days=5)
        assert _due_for_rescan(scan, 30) is False

    def test_old_scan_is_due(self) -> None:
        scan = MagicMock()
        scan.scanned_at = datetime.now(UTC) - timedelta(days=31)
        assert _due_for_rescan(scan, 30) is True

    def test_exactly_on_boundary_is_due(self) -> None:
        scan = MagicMock()
        scan.scanned_at = datetime.now(UTC) - timedelta(days=30)
        assert _due_for_rescan(scan, 30) is True

    def test_naive_datetime_treated_as_utc(self) -> None:
        scan = MagicMock()
        scan.scanned_at = datetime.utcnow() - timedelta(days=60)
        assert _due_for_rescan(scan, 30) is True


# ---------------------------------------------------------------------------
# Integration: run_monitor
# ---------------------------------------------------------------------------


class TestRunMonitor:
    def test_clean_result_recorded(self, tmp_db, minimal_config, web_broker) -> None:
        def page_factory(b):
            return _mock_page(["John Smith, Dallas TX"])

        summary = run_monitor(minimal_config, brokers=[web_broker], _page_factory=page_factory)

        assert summary.scanned == 1
        assert summary.clean == 1
        assert summary.found == 0
        assert summary.requeued == 0
        assert summary.reports[0].result == "clean"

    def test_found_creates_queued_submission(self, tmp_db, minimal_config, web_broker) -> None:
        def page_factory(b):
            return _mock_page(["Jane Q Public, 123 Main St, Austin TX"])

        summary = run_monitor(minimal_config, brokers=[web_broker], _page_factory=page_factory)

        assert summary.found == 1
        assert summary.requeued == 1
        assert summary.reports[0].requeued is True

    def test_found_with_active_submission_does_not_requeue(
        self, tmp_db, minimal_config, web_broker
    ) -> None:
        with get_session() as session:
            create_submission(
                session,
                broker_slug=web_broker.slug,
                method_used=web_broker.method,
                legal_basis_cited=web_broker.legal_basis,
                statutory_response_days=web_broker.statutory_response_days,
            )

        def page_factory(b):
            return _mock_page(["Jane Q Public, Austin TX"])

        summary = run_monitor(minimal_config, brokers=[web_broker], _page_factory=page_factory)

        assert summary.found == 1
        assert summary.requeued == 0

    def test_broker_without_scan_config_is_inconclusive(
        self, tmp_db, minimal_config, email_broker
    ) -> None:
        summary = run_monitor(minimal_config, brokers=[email_broker])
        assert summary.inconclusive == 1
        assert summary.scanned == 1

    def test_skip_recently_scanned_broker(self, tmp_db, minimal_config, web_broker) -> None:
        with get_session() as session:
            record_scan(session, web_broker.slug, "clean")

        summary = run_monitor(minimal_config, brokers=[web_broker])
        assert summary.scanned == 0
        assert len(summary.reports) == 1
        assert summary.reports[0].result == "skipped"

    def test_multiple_brokers_mixed_results(
        self, tmp_db, minimal_config, web_broker, email_broker
    ) -> None:
        call_count = [0]

        def page_factory(b):
            call_count[0] += 1
            return _mock_page(["Jane Q Public, Austin TX"])

        summary = run_monitor(
            minimal_config, brokers=[web_broker, email_broker], _page_factory=page_factory
        )
        assert summary.scanned == 2
        assert summary.found == 1  # web_broker: found
        assert summary.inconclusive == 1  # email_broker: no scan config
        assert call_count[0] == 1  # only web_broker has scan config

    def test_scan_result_written_to_db(self, tmp_db, minimal_config, web_broker) -> None:
        def page_factory(b):
            return _mock_page(["Jane Q Public, Austin TX"])

        run_monitor(minimal_config, brokers=[web_broker], _page_factory=page_factory)

        with get_session() as session:
            last = get_last_scan(session, web_broker.slug)
        assert last is not None
        assert last.result == "found"
        assert last.matches_json is not None

    def test_monitor_summary_properties(self) -> None:
        s = MonitorSummary(
            reports=[
                BrokerScanReport("a", "A", "found", requeued=True),
                BrokerScanReport("b", "B", "clean"),
                BrokerScanReport("c", "C", "inconclusive"),
                BrokerScanReport("d", "D", "skipped"),
            ]
        )
        assert s.scanned == 3
        assert s.found == 1
        assert s.clean == 1
        assert s.inconclusive == 1
        assert s.requeued == 1
