"""Structured logging for optout: dual-channel (JSON file + optional console).

Usage
-----
Call configure_logging() once at CLI startup (in the submit command).
Everywhere else, call get_logger() to obtain a bound logger.

Context injection
-----------------
Use structlog.contextvars.bind_contextvars(submission_id=..., step_id=...)
to attach fields that appear on every subsequent log record in that context.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

import structlog

# Wire structlog to use stdlib as its backend.  This means:
#   - get_logger() returns a stdlib-backed logger
#   - The stdlib root logger's default WARNING level silences debug output
#     unless configure_logging() has been called — tests stay clean.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=False,
)


def _log_dir() -> Path:
    xdg = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = xdg / "optout" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def configure_logging(*, verbose: bool = False) -> None:
    """Add handlers to the root logger.

    File handler: JSON at DEBUG, daily rotation, always active.
    Console handler: human-readable at DEBUG, only when verbose=True.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # JSON file — always, every level
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(_log_dir() / "optout.log"),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
        )
    )
    root.addHandler(file_handler)

    if verbose:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.dev.ConsoleRenderer(colors=True),
            )
        )
        root.addHandler(console_handler)


def get_logger(name: str = "optout") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
