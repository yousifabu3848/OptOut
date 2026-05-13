"""Tests for `optout verify` and the verify module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from optout.cli import app

runner = CliRunner()

# sync_playwright is a local import inside verify_broker() — patch at source.
_PW = "playwright.sync_api.sync_playwright"


def _mock_pw_factory(selectors_present: set[str]):
    """Return a context-manager mock whose page.query_selector hits *selectors_present*."""
    mock = MagicMock()
    page = MagicMock()

    def _query_selector(sel, **_kw):
        return MagicMock() if sel in selectors_present else None

    page.query_selector.side_effect = _query_selector
    page.goto.return_value = None

    browser = MagicMock()
    browser.new_page.return_value = page

    pw_ctx = MagicMock()
    pw_ctx.chromium.launch.return_value = browser

    mock.return_value.__enter__.return_value = pw_ctx
    return mock


@pytest.fixture()
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wire XDG + broker dir so verify runs in isolation with a real broker YAML."""

    import yaml

    from optout.brokers import registry as reg_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTOUT_BROKERS_DIR", str(tmp_path / "brokers"))
    monkeypatch.setenv("COLUMNS", "200")

    reg_mod._registry.clear()
    reg_mod._loaded = False

    (tmp_path / "brokers").mkdir()

    broker_yaml = {
        "slug": "test-broker",
        "name": "Test Broker",
        "domain": "test.example.com",
        "category": "people_search",
        "method": "web_form",
        "opt_out_url": "https://test.example.com/optout",
        "legal_basis": ["CCPA"],
        "statutory_response_days": 45,
        "last_verified_at": "2026-01-01",
        "steps": [
            {
                "id": "fill_name",
                "type": "form_fill",
                "fields": {
                    "first_name": {"selector": "input[name='first']", "value": "{name_first}"},
                    "last_name": {"selector": "input[name='last']", "value": "{name_last}"},
                },
            },
            {
                "id": "submit",
                "type": "click",
                "selector": "button[type='submit']",
            },
        ],
    }
    (tmp_path / "brokers" / "test-broker.yml").write_text(yaml.dump(broker_yaml))

    yield tmp_path

    reg_mod._registry.clear()
    reg_mod._loaded = False


class TestVerifyCommand:
    def test_all_pass(self, _env: Path) -> None:
        all_selectors = {
            "input[name='first']",
            "input[name='last']",
            "button[type='submit']",
        }
        with patch(_PW, _mock_pw_factory(all_selectors)):
            result = runner.invoke(app, ["verify"])

        assert result.exit_code == 0
        assert "✅" in result.output

    def test_missing_selectors_warns(self, _env: Path) -> None:
        # Only the submit button found — name fields missing.
        with patch(_PW, _mock_pw_factory({"button[type='submit']"})):
            result = runner.invoke(app, ["verify"])

        # warn → still exits 0 (only ❌ exits 1)
        assert result.exit_code == 0
        assert "⚠" in result.output

    def test_page_load_fail_exits_one(self, _env: Path) -> None:
        mock = MagicMock()
        pw_ctx = MagicMock()
        browser = MagicMock()
        page = MagicMock()
        page.goto.side_effect = Exception("net::ERR_NAME_NOT_RESOLVED")
        browser.new_page.return_value = page
        pw_ctx.chromium.launch.return_value = browser
        mock.return_value.__enter__.return_value = pw_ctx

        with patch(_PW, mock):
            result = runner.invoke(app, ["verify"])

        assert result.exit_code == 1
        assert "❌" in result.output

    def test_broker_filter(self, _env: Path) -> None:
        """--broker unknown-slug exits 1 with error."""
        result = runner.invoke(app, ["verify", "--broker", "no-such-broker"])
        assert result.exit_code == 1

    def test_update_flag_rewrites_yaml(self, _env: Path) -> None:
        all_selectors = {
            "input[name='first']",
            "input[name='last']",
            "button[type='submit']",
        }
        with patch(_PW, _mock_pw_factory(all_selectors)):
            result = runner.invoke(app, ["verify", "--update"])

        assert result.exit_code == 0
        from datetime import date

        today = date.today().isoformat()
        yaml_content = (_env / "brokers" / "test-broker.yml").read_text()
        assert f"last_verified_at: {today}" in yaml_content


class TestVerifyModule:
    def test_collect_selectors_form_fill(self) -> None:
        from optout.brokers.schema import FormField, FormFillStep
        from optout.verify import _collect_selectors

        step = FormFillStep(
            id="fill",
            type="form_fill",
            fields={
                "name": FormField(selector="input#name", value="x"),
                "email": FormField(selector="input#email", value="y"),
            },
        )

        class FakeBroker:
            steps = [step]

        items = _collect_selectors(FakeBroker())  # type: ignore[arg-type]
        selectors = [s for _, _, s in items]
        assert "input#name" in selectors
        assert "input#email" in selectors

    def test_collect_selectors_click(self) -> None:
        from optout.brokers.schema import ClickStep
        from optout.verify import _collect_selectors

        step = ClickStep(id="btn", type="click", selector="button.submit")

        class FakeBroker:
            steps = [step]

        items = _collect_selectors(FakeBroker())  # type: ignore[arg-type]
        assert any(s == "button.submit" for _, _, s in items)

    def test_update_last_verified_at(self, tmp_path: Path) -> None:
        from datetime import date

        from optout.verify import update_last_verified_at

        yaml_file = tmp_path / "broker.yml"
        yaml_file.write_text("slug: foo\nlast_verified_at: 2025-01-01\n")
        update_last_verified_at(yaml_file, today=date(2026, 6, 1))
        assert "last_verified_at: 2026-06-01" in yaml_file.read_text()

    def test_update_adds_field_if_missing(self, tmp_path: Path) -> None:
        from datetime import date

        from optout.verify import update_last_verified_at

        yaml_file = tmp_path / "broker.yml"
        yaml_file.write_text("slug: foo\nname: Foo\n")
        update_last_verified_at(yaml_file, today=date(2026, 6, 1))
        assert "last_verified_at: 2026-06-01" in yaml_file.read_text()

    def test_no_opt_out_url_returns_warn(self) -> None:
        from optout.verify import verify_broker

        class FakeBroker:
            slug = "no-url"
            name = "No URL"
            opt_out_url = None
            steps = []

        result = verify_broker(FakeBroker())  # type: ignore[arg-type]
        assert result.status == "warn"
        assert result.error is not None
