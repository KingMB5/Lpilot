"""LLM access layer — OpenAI-compatible client via env, FakeLLM for offline/tests.

AGENTS.md rules enforced here:
- LLM only through env LLM_BASE_URL / LLM_API_KEY / LLM_MODEL (openai SDK).
- JSON outputs are schema-validated (pydantic) with ONE retry, then a
  deterministic fallback supplied by the caller.
- Every prompt is redacted through the session SecretRegister before sending.
- Per-run token budget (approximate, chars/4) enforced in code.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ValidationError

from app.redact import SecretRegister, redact_text

FALLBACK_NOTICE = "(deterministic fallback — LLM output unusable)"


class LLMError(Exception):
    pass


class BudgetExceededError(LLMError):
    pass


class RunBudget:
    """Approximate per-run token budget enforced in code (never trust the model)."""

    def __init__(self, cap_tokens: int) -> None:
        self.cap = int(cap_tokens)
        self.used = 0

    def charge(self, text: str) -> int:
        approx = max(1, len(text) // 4)
        if self.used + approx > self.cap:
            raise BudgetExceededError(f"run budget {self.cap} exceeded at {self.used}+{approx}")
        self.used += approx
        return approx


class LLMClient(Protocol):
    name: str

    def complete_text(self, system: str, user: str) -> str: ...

    def complete_json(self, system: str, user: str, model_cls: type[BaseModel],
                      fallback: Callable[[], dict]) -> dict: ...


class _Base:
    def __init__(self, register: SecretRegister, budget: RunBudget) -> None:
        self.register = register
        self.budget = budget

    def _prepare(self, system: str, user: str) -> tuple[str, str]:
        s = redact_text(system, self.register)
        u = redact_text(user, self.register)
        self.budget.charge(s + u)
        return s, u


def _validate_with_retry(client, system: str, user: str, model_cls, fallback):
    """Ask for JSON, validate against pydantic; ONE retry with the error; then fallback."""
    last_err = ""
    for attempt in (1, 2):
        raw = client._raw_text(
            system + "\nRespond with ONLY minified JSON matching the schema.",
            user if attempt == 1 else user + f"\nPrevious attempt failed validation: {last_err}",
        )
        try:
            data = json.loads(_strip_code_fence(raw))
            return model_cls.model_validate(data).model_dump()
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = str(e)[:300]
    out = fallback()
    out.setdefault("fallback_notice", FALLBACK_NOTICE)
    return out


def _strip_code_fence(raw: str) -> str:
    t = raw.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    return t.strip()


class OpenAICompatClient(_Base):
    """Real client — BoxAPI or any OpenAI-compatible endpoint via env vars."""

    name = "openai-compat"

    def __init__(self, register: SecretRegister, budget: RunBudget) -> None:
        super().__init__(register, budget)
        base = os.getenv("LLM_BASE_URL")
        key = os.getenv("LLM_API_KEY")
        self.model = os.getenv("LLM_MODEL")
        if not (base and key and self.model):
            raise LLMError("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL must be set in env")
        from openai import OpenAI  # imported lazily; not needed in demo mode

        self._client = OpenAI(base_url=base, api_key=key,
                              timeout=float(os.getenv("LLM_TIMEOUT_S", "30")))

    def _raw_text(self, system: str, user: str) -> str:
        s, u = self._prepare(system, user)
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": s}, {"role": "user", "content": u}],
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""

    def complete_text(self, system: str, user: str) -> str:
        return self._raw_text(system, user)

    def complete_json(self, system, user, model_cls, fallback):
        return _validate_with_retry(self, system, user, model_cls, fallback)


class CachedClient(_Base):
    """Demo-mode response cache (identical prompt -> identical answer, zero cost)."""

    def __init__(self, inner: LLMClient, register: SecretRegister, budget: RunBudget) -> None:
        super().__init__(register, budget)
        self.inner = inner
        self.name = f"cached({inner.name})"
        self._cache: dict[str, str] = {}

    def _key(self, system: str, user: str) -> str:
        return hashlib.sha256((system + "\x00" + user).encode()).hexdigest()

    def _raw_text(self, system: str, user: str) -> str:
        # budget/charge and redaction are owned by the inner client; we only memoize
        s, u = self._prepare(system, user)
        self.budget.used -= max(1, (len(s) + len(u)) // 4)  # undo wrapper charge, inner re-charges
        k = self._key(s, u)
        if k not in self._cache:
            self._cache[k] = self.inner.complete_text(system, user)
        return self._cache[k]

    def complete_text(self, system: str, user: str) -> str:
        return self._raw_text(system, user)

    def complete_json(self, system, user, model_cls, fallback):
        return _validate_with_retry(self, system, user, model_cls, fallback)


class FakeLLM(_Base):
    """Deterministic offline client for tests and demo mode."""

    name = "fake"

    def __init__(self, register: SecretRegister, budget: RunBudget, concepts: dict | None = None) -> None:
        super().__init__(register, budget)
        self.concepts = concepts or {}

    def _raw_text(self, system: str, user: str) -> str:  # pragma: no cover - used via explain
        return "fake"

    def complete_text(self, system: str, user: str) -> str:
        return self._raw_text(system, user)

    def complete_json(self, system, user, model_cls, fallback):
        out = fallback()  # deterministic JSON = the deterministic plan builder
        return model_cls.model_validate(out).model_dump()


def get_client(register: SecretRegister, budget: RunBudget, demo_mode: bool, concepts: dict | None = None) -> LLMClient:
    """Factory: DEMO_MODE=1 forces offline; missing env degrades to FakeLLM + flag."""
    if demo_mode:
        return FakeLLM(register, budget, concepts)
    try:
        return OpenAICompatClient(register, budget)
    except LLMError:
        return FakeLLM(register, budget, concepts)
