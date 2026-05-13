"""Tests for `optout brokers list` and `optout brokers info`."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from optout.brokers import registry as reg_module
from optout.cli import app

FIXTURES = Path(__file__).parent / "fixtures" / "brokers"

runner = CliRunner()


@pytest.fixture(autouse=True)
def _load_fixture_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the broker registry at the test fixtures directory for every test."""
    monkeypatch.setenv("OPTOUT_BROKERS_DIR", str(FIXTURES))
    monkeypatch.setenv("COLUMNS", "200")
    # Reset registry state between tests
    reg_module._registry.clear()
    reg_module._loaded = False


class TestBrokersList:
    def test_shows_both_fixture_brokers(self) -> None:
        result = runner.invoke(app, ["brokers", "list"])
        assert result.exit_code == 0
        assert "test-email-broker" in result.output
        assert "test-web-broker" in result.output

    def test_shows_method_column(self) -> None:
        result = runner.invoke(app, ["brokers", "list"])
        assert result.exit_code == 0
        assert "email" in result.output
        assert "web_form" in result.output

    def test_category_filter_matches(self) -> None:
        result = runner.invoke(app, ["brokers", "list", "--category", "people_search"])
        assert result.exit_code == 0
        assert "test-email-broker" in result.output

    def test_category_filter_no_match(self) -> None:
        result = runner.invoke(app, ["brokers", "list", "--category", "credit_bureau"])
        assert result.exit_code == 0
        assert "No brokers found in category" in result.output

    def test_shows_legal_basis(self) -> None:
        result = runner.invoke(app, ["brokers", "list"])
        assert result.exit_code == 0
        assert "CCPA" in result.output

    def test_shows_statutory_days(self) -> None:
        result = runner.invoke(app, ["brokers", "list"])
        assert result.exit_code == 0
        # email fixture has 30 days, web_form has 45
        assert "30" in result.output
        assert "45" in result.output

    def test_count_line(self) -> None:
        result = runner.invoke(app, ["brokers", "list"])
        assert result.exit_code == 0
        assert "2 broker(s) listed" in result.output


class TestBrokersInfo:
    def test_email_broker_overview(self) -> None:
        result = runner.invoke(app, ["brokers", "info", "test-email-broker"])
        assert result.exit_code == 0
        assert "Test Email Broker" in result.output
        assert "example-email.com" in result.output
        assert "optout@example-email.com" in result.output
        assert "30 days" in result.output

    def test_web_broker_shows_steps(self) -> None:
        result = runner.invoke(app, ["brokers", "info", "test-web-broker"])
        assert result.exit_code == 0
        assert "Steps (6)" in result.output
        assert "find_listing" in result.output
        assert "form_fill" in result.output
        assert "capture" in result.output

    def test_requirements_section(self) -> None:
        result = runner.invoke(app, ["brokers", "info", "test-web-broker"])
        assert result.exit_code == 0
        assert "Requirements" in result.output
        assert "Listing URL" in result.output

    def test_notes_shown(self) -> None:
        result = runner.invoke(app, ["brokers", "info", "test-email-broker"])
        assert result.exit_code == 0
        assert "Notes" in result.output
        assert "unit tests" in result.output

    def test_unknown_slug_exits_nonzero(self) -> None:
        result = runner.invoke(app, ["brokers", "info", "no-such-broker"])
        assert result.exit_code != 0
        assert "Unknown broker slug" in result.output

    def test_no_db_shows_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nonexistent"))
        result = runner.invoke(app, ["brokers", "info", "test-email-broker"])
        assert result.exit_code == 0
        assert "Database not initialised" in result.output

    def test_submission_status_shown_when_db_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        from sqlmodel import create_engine

        import optout.db as db_mod
        from optout.db import create_submission, get_session, init_db

        db_file = tmp_path / "optout" / "optout.db"
        db_file.parent.mkdir(parents=True)
        engine = create_engine(f"sqlite:///{db_file}")
        init_db(engine)

        # Monkeypatch get_engine to use our test engine
        monkeypatch.setattr(db_mod, "get_engine", lambda path=None: engine)

        with get_session(engine) as session:
            create_submission(
                session,
                broker_slug="test-email-broker",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )

        result = runner.invoke(app, ["brokers", "info", "test-email-broker"])
        assert result.exit_code == 0
        assert "queued" in result.output
