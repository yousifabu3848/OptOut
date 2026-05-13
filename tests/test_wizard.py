"""Tests for wizard helpers, celebrity guard, and config round-trip."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import typer
import yaml

from optout.config import (
    Address,
    EmailConfig,
    EmailSet,
    MonitoringConfig,
    PhoneSet,
    PlaywrightConfig,
    PreviousAddress,
    Profile,
    SmtpConfig,
    UserConfig,
    load_config,
    save_config,
)
from optout.wizard import assert_own_identity

# ---------------------------------------------------------------------------
# Celebrity guard
# ---------------------------------------------------------------------------


class TestCelebrityGuard:
    def test_known_celebrity_raises(self) -> None:
        with pytest.raises(typer.Exit):
            assert_own_identity("Taylor Swift")

    def test_case_insensitive(self) -> None:
        with pytest.raises(typer.Exit):
            assert_own_identity("ELON MUSK")

    def test_ordinary_name_passes(self) -> None:
        assert_own_identity("Jane Q Public")  # must not raise

    def test_partial_match_does_not_trigger(self) -> None:
        # "swift" alone or a longer name should not trigger the guard
        assert_own_identity("Taylor Swiftly")


# ---------------------------------------------------------------------------
# Config round-trip (save → load)
# ---------------------------------------------------------------------------


def _make_config() -> UserConfig:
    return UserConfig(
        profile=Profile(
            legal_name="Jane Q Public",
            aliases=["Janie Public"],
            dob=date(1990, 5, 14),
            current_address=Address(street="123 Main St", city="Austin", state="TX", zip="78701"),
            previous_addresses=[
                PreviousAddress(
                    street="456 Oak Ave",
                    city="Dallas",
                    state="TX",
                    zip="75201",
                    **{"from": 2015, "to": 2020},
                )
            ],
            phones=PhoneSet(current=["+15125551234"], previous=["+15125559999"]),
            emails=EmailSet(current=["jane@example.com"], previous=["jane.old@example.com"]),
            relatives=["John Public"],
        ),
        email=EmailConfig(
            method="smtp",
            smtp=SmtpConfig(
                host="smtp.gmail.com",
                port=587,
                username="jane@example.com",
                password_env="OPTOUT_SMTP_PASSWORD",
            ),
        ),
        playwright=PlaywrightConfig(headless=False, slow_mo_ms=0),
        monitoring=MonitoringConfig(rescan_interval_days=30),
    )


class TestConfigRoundTrip:
    def test_save_and_load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_config()
        save_config(cfg)

        loaded = load_config()
        assert loaded.profile.legal_name == "Jane Q Public"
        assert loaded.profile.dob == date(1990, 5, 14)
        assert loaded.profile.aliases == ["Janie Public"]
        assert loaded.profile.phones.current == ["+15125551234"]
        assert loaded.email.method == "smtp"
        assert loaded.email.smtp is not None
        assert loaded.email.smtp.host == "smtp.gmail.com"
        assert loaded.monitoring.rescan_interval_days == 30

    def test_previous_address_round_trip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_config()
        save_config(cfg)

        # Check YAML uses "from"/"to" (not "from_year"/"to_year")
        raw = yaml.safe_load((tmp_path / "optout" / "config.yml").read_text())
        prev = raw["profile"]["previous_addresses"][0]
        assert "from" in prev, "expected 'from' key in YAML, got from_year"
        assert "to" in prev, "expected 'to' key in YAML, got to_year"
        assert prev["from"] == 2015
        assert prev["to"] == 2020

        loaded = load_config()
        addr = loaded.profile.previous_addresses[0]
        assert addr.from_year == 2015
        assert addr.to_year == 2020

    def test_config_file_permissions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        save_config(_make_config())
        p = tmp_path / "optout" / "config.yml"
        # octal permissions: 0o100600 = regular file, owner rw only
        assert oct(p.stat().st_mode)[-3:] == "600"

    def test_missing_config_raises_friendly_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with pytest.raises(FileNotFoundError, match="optout init"):
            load_config()
