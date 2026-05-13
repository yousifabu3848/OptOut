"""Shared types used by the dispatcher and every method handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SubmissionResult:
    payload: dict[str, Any]
    artifact_path: str | None = None


class UserInputRequired(Exception):
    """Raised when the submission cannot continue without input from the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SubmissionFailed(Exception):
    """Raised for unrecoverable submission errors (broker changed their form, etc.)."""
