"""In-memory broker catalog, populated from the brokers/ YAML directory."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

from .loader import load_all_brokers
from .schema import BrokerDef

_registry: dict[str, BrokerDef] = {}
_loaded = False


def _brokers_dir() -> Path:
    """Return the directory containing broker YAML files.

    Priority:
    1. OPTOUT_BROKERS_DIR env var — lets users/tests override with a custom set.
    2. importlib.resources — finds the YAMLs bundled inside the installed package,
       works for both editable installs (real filesystem path) and wheel installs.
    """
    env = os.environ.get("OPTOUT_BROKERS_DIR")
    if env:
        return Path(env)
    return Path(str(files("optout").joinpath("data/brokers")))


def load_registry(brokers_dir: Path | None = None) -> None:
    global _registry, _loaded
    directory = brokers_dir or _brokers_dir()
    if not directory.exists():
        _registry = {}
        _loaded = True
        return
    _registry = {b.slug: b for b in load_all_brokers(directory)}
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        load_registry()


def get_broker(slug: str) -> BrokerDef:
    _ensure_loaded()
    try:
        return _registry[slug]
    except KeyError:
        raise KeyError(f"Unknown broker slug: '{slug}'. Run `optout brokers list` to see options.")


def all_brokers() -> list[BrokerDef]:
    _ensure_loaded()
    return sorted(_registry.values(), key=lambda b: b.name)
