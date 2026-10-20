"""Secret redaction — security core used before ANY LLM call, log line or SSE payload.

Rules (AGENTS.md): never print, log or commit secrets. Values registered here are
replaced with ***REDACTED*** everywhere they could leak.
"""

from __future__ import annotations

import re

from app.config import load_defaults

MASK = "***REDACTED***"

_ASSIGN_RE = re.compile(
    r"""(?P<key>[A-Za-z_$][A-Za-z0-9_$-]*(?:password|passwd|secret|token|api_?key)[A-Za-z0-9_$-]*)"""
    r"""(?P<eq>\s*[:=]=?\s*)(?P<q>["']?)(?P<val>[^\s"']{4,})(?P=q)""",
    re.IGNORECASE,
)


def _cfg() -> dict:
    return load_defaults().get("security", {})


def _key_is_secret(key: str) -> bool:
    k = key.lower()
    return any(p in k for p in _cfg().get("secret_key_patterns", ["password", "secret", "token", "api_key"]))


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return any(v.startswith(p) for p in _cfg().get("placeholder_value_prefixes", ["changeme"]))


class SecretRegister:
    """Collects secret VALUES once per session; redaction replaces them everywhere."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, value: str) -> None:
        v = (value or "").strip()
        min_len = int(_cfg().get("secret_min_value_len", 6))
        if len(v) >= min_len and not _is_placeholder(v):
            self._values.add(v)

    def add_from_env_example(self, env_example: dict[str, str]) -> None:
        for key, value in env_example.items():
            if _key_is_secret(key):
                self.add(value)

    def add_from_texts(self, texts: dict[str, str]) -> None:
        """Scan code/config texts for secret-looking assignments and register values."""
        for _path, text in texts.items():
            for m in _ASSIGN_RE.finditer(text):
                if _key_is_secret(m.group("key")):
                    self.add(m.group("val"))

    @property
    def values(self) -> list[str]:
        return sorted(self._values, key=len, reverse=True)  # longest first

    def __len__(self) -> int:
        return len(self._values)


def redact_text(text: str, register: SecretRegister) -> str:
    out = text
    for v in register.values:
        if v in out:
            out = out.replace(v, MASK)
    # mask leftover `KEY=value` / `KEY: value` assignments for secret-ish keys
    out = _ASSIGN_RE.sub(lambda m: f"{m.group('key')}{m.group('eq')}{MASK}" if _key_is_secret(m.group("key")) else m.group(0), out)
    return out


def redact_obj(obj, register: SecretRegister):
    """Recursively redact every string inside dicts/lists."""
    if isinstance(obj, str):
        return redact_text(obj, register)
    if isinstance(obj, dict):
        return {k: redact_obj(v, register) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, register) for v in obj]
    return obj
