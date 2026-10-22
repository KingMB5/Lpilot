"""T6 — LLM layer: FakeLLM explanations, budget, caching, redaction-before-send."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.redact import SecretRegister, redact_obj, redact_text
from llm.client import BudgetExceededError, CachedClient, FakeLLM, RunBudget
from llm.explain import estimate_cost, explain, fake_explain, prices

CONCEPTS = {c["key"]: c for c in __import__("json").loads(
    (__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "concepts.json").read_text(encoding="utf-8")
)["concepts"]}

RISK_R1 = {
    "id": "R1_sqlite_without_disk",
    "severity": "high",
    "evidence_file": "prisma/schema.prisma",
    "concept_keys": ["database", "persistent_disk", "plan_resources"],
    "detail": "SQLite بدون دیسک ماندگار از بین می‌رود.",
}


def test_prices_all_unverified():
    p = prices()
    assert str(p["source_url"]).startswith("https://")
    assert p["checked"] == "UNVERIFIED"
    for plan in p["plans"].values():
        for v in plan.values():
            assert str(v) == "UNVERIFIED"
    est = estimate_cost()
    assert est["cost_unverified"] is True


def test_explain_levels_differ_visibly():
    b = fake_explain(RISK_R1, "beginner", CONCEPTS)
    e = fake_explain(RISK_R1, "expert", CONCEPTS)
    assert b != e
    assert "•" in b and "UNVERIFIED" in b          # beginner teaches with bullets
    assert "•" not in e                             # expert terse, no bullet list
    assert e.count("\n") <= 1                       # expert: max 2 lines
    assert "دیسک ماندگار" in b                      # uses the glossary concept
    assert "UNVERIFIED" in e


def test_explain_dispatches_fake():
    reg, bud = SecretRegister(), RunBudget(100000)
    client = FakeLLM(reg, bud, CONCEPTS)
    assert explain(client, RISK_R1, "beginner", CONCEPTS) == fake_explain(RISK_R1, "beginner", CONCEPTS)


def test_budget_cap_enforced():
    reg = SecretRegister()
    bud = RunBudget(10)
    client = FakeLLM(reg, bud, CONCEPTS)
    with pytest.raises(BudgetExceededError):
        client._prepare("x" * 100, "y" * 100)
    assert bud.used <= bud.cap


class _Counting:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def complete_text(self, system, user):
        self.calls += 1
        return f"ans{self.calls}"


def test_cached_client_memoizes():
    reg, bud = SecretRegister(), RunBudget(100000)
    inner = _Counting()
    cached = CachedClient(inner, reg, bud)
    a = cached.complete_text("s", "u")
    b = cached.complete_text("s", "u")
    assert a == b == "ans1"
    assert inner.calls == 1
    assert cached.name == "cached(counting)"


def test_redaction_masks_registered_values_and_assignments():
    reg = SecretRegister()
    reg.add_from_env_example({"ADMIN_PASSWORD": "benis1404", "PORT": "3000"})
    reg.add_from_texts({"lib/auth.ts": 'const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "benis1404";'})
    assert "benis1404" in reg.values
    text = "config uses benis1404 and ADMIN_PASSWORD=benis1404 inside logs"
    out = redact_text(text, reg)
    assert "benis1404" not in out
    assert "***REDACTED***" in out
    obj = redact_obj({"cmd": "ADMIN_PASSWORD: benis1404", "n": 3, "l": ["benis1404"]}, reg)
    assert "benis1404" not in str(obj)
    assert obj["n"] == 3


def test_redaction_ignores_placeholders_and_short_values():
    reg = SecretRegister()
    reg.add_from_env_example({"API_TOKEN": "changeme-please", "PIN": "123"})
    assert reg.values == []


def test_schema_retry_then_fallback(monkeypatch):
    from llm import client as cl


    class Boom:
        name = "boom"

        def _raw_text(self, system, user):
            return "not-json at all"

    calls = {"n": 0}

    def counting_raw(self, system, user):
        calls["n"] += 1
        return "garbage {{"

    monkeypatch.setattr(Boom, "_raw_text", counting_raw)

    class Tiny(BaseModel):
        x: int

    out = cl._validate_with_retry(Boom(), "s", "u", Tiny, lambda: {"x": 7})
    assert out == {"x": 7, "fallback_notice": cl.FALLBACK_NOTICE}
    assert calls["n"] == 2  # exactly one retry, then deterministic fallback
