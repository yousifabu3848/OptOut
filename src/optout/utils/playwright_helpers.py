"""Shared Playwright utilities used across method handlers."""

from __future__ import annotations

from typing import Any


def query_selector_safe(page: Any, selector: str, timeout_ms: int = 5_000) -> Any | None:
    """Return the first matching element or None (never raises on timeout)."""
    try:
        return page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
    except Exception:
        return None


def is_visible(page: Any, selector: str) -> bool:
    """Return True if *selector* exists and is visible in the current page."""
    el = page.query_selector(selector)
    return el is not None and el.is_visible()


def safe_fill(page: Any, selector: str, value: str, timeout_ms: int = 10_000) -> bool:
    """
    Fill *selector* with *value*.  Returns True on success, False if the
    selector was not found within the timeout.
    """
    el = query_selector_safe(page, selector, timeout_ms)
    if el is None:
        return False
    el.fill(value)
    return True


def safe_click(page: Any, selector: str, timeout_ms: int = 10_000) -> bool:
    """
    Click *selector*.  Returns True on success, False if not found.
    """
    el = query_selector_safe(page, selector, timeout_ms)
    if el is None:
        return False
    el.click()
    return True
