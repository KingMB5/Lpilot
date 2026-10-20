"""Central configuration loader for LiaraPilot.

Reads config/defaults.yaml once. The file separates VERIFIED facts (with source
URL + checked date) from UNVERIFIED ones. Code must never invent values that are
not present here.
"""

from __future__ import annotations

import functools
import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULTS_PATH = ROOT / "config" / "defaults.yaml"


@functools.lru_cache(maxsize=1)
def load_defaults(path: str | None = None) -> dict[str, Any]:
    p = pathlib.Path(path) if path else DEFAULTS_PATH
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def limits() -> dict[str, Any]:
    return load_defaults().get("limits", {})


def demo_cfg() -> dict[str, Any]:
    return load_defaults().get("demo", {})
