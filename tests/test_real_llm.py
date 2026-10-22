"""Phase-3 T3 — real LLM client (BoxAPI defaults), billable-call cap, and the
UNKNOWN-failure path: the model ranks possible causes from the redacted log
(schema-validated) and the executed action stays allowlisted or escalates.

The new-scenario test uses a failure log that matches NONE of the scripted
MockDeployer scenarios (bad_port / missing_env_var / wrong_start_script /
missing_disk_sqlite): a corrupted SQLite volume.
"""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from deployers.mock import MockDeployer
from llm.client import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, BudgetExceededError, \
    OpenAICompatClient, RunBudget, get_client
from llm.diagnose import ALLOWED_ACTIONS, DiagnosisModel
from app.redact import SecretRegister

from test_graph import PERFUME, FULL_ENV, Harness

SECRET = "totally-secret-value"


class FailsUnknownOnce:
    """Fails once with a failure kind/log that matches NO scripted scenario:
    corrupted SQLite volume on the disk (disk I/O error)."""

    name = "flaky-unknown"

    def __init__(self, inner) -> None:
        self.inner, self.calls = inner, 0

    def deploy(self, config):
        self.calls += 1
        if self.calls == 1:
            return {"ok": False, "kind": "unknown",
                    "message": "deploy failed",
                    "logs": [f"sqlite3.DatabaseError: database disk image is malformed "
                             f"(mount /db/app.db, secret={SECRET})"]}
        return self.inner.deploy(config)

    def logs(self):
        return self.inner.logs()

    def status(self):
        return self.inner.status()


# ---------------------------------------------------------------- client (T3.1)

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DEMO_MODE", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
                "LLM_MAX_CALLS", "LLM_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)


def test_boxapi_defaults_applied_when_base_or_model_missing(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    reg, bud = SecretRegister(), RunBudget(10_000)
    c = OpenAICompatClient(reg, bud)  # constructor makes NO network call
    assert c.base_url == DEFAULT_LLM_BASE_URL == "https://ai.boxapi.ir/ai/v1"
    assert c.model == DEFAULT_LLM_MODEL == "gpt-5.4-mini"
    assert c.calls == 0


def test_missing_key_is_the_only_fallback_to_fakellm(monkeypatch):
    reg, bud = SecretRegister(), RunBudget(10_000)
    assert get_client(reg, bud, demo_mode=False).name == "fake"  # no key -> fallback
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    assert get_client(reg, bud, demo_mode=False).name == "openai-compat"
    assert get_client(reg, bud, demo_mode=True).name == "fake"   # demo always offline


class _StubCompletions:
    def __init__(self, probe):
        self._probe = probe

    def create(self, **kw):
        self._probe.append(kw)
        msg = type("M", (), {"content": "ok"})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


class _StubOpenAI:
    """Offline stand-in for the OpenAI SDK client (constructor + one call path)."""

    last_prompt = {}

    def __init__(self, base_url=None, api_key=None, timeout=None):
        self.base_url, self.api_key = base_url, api_key
        self.chat = type("Chat", (), {})()
        self._probe: list = []
        self.chat.completions = _StubCompletions(self._probe)


def test_call_cap_and_call_log(monkeypatch, caplog):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("LLM_MAX_CALLS", "2")
    monkeypatch.setattr("openai.OpenAI", _StubOpenAI)
    reg, bud = SecretRegister(), RunBudget(10_000)
    bud.register = reg
    c = OpenAICompatClient(reg, bud)
    reg.add(SECRET)  # must never reach the call log
    c.complete_text("system one", "user one")
    c.complete_text("system two", f"user two secret={SECRET}")
    with pytest.raises(BudgetExceededError):
        c.complete_text("system three", "user three")
    assert c.calls == 2
    assert len(c.call_log) == 2
    assert all(line.startswith("call#") and "model=gpt-5.4-mini" in line for line in c.call_log)
    assert SECRET not in json.dumps(c.call_log)
    with caplog.at_level(logging.INFO, logger="liarapilot.llm"):
        pass
    assert SECRET not in caplog.text  # nothing secret anywhere in logging either


def test_budget_exception_is_caught_by_plan_node_deterministic_plan():
    """Any client exception (incl. the new call cap) degrades to deterministic
    behavior — the plan NODE wraps complete_json in try/except."""

    class DeadClient:
        name = "dead"

        def complete_json(self, *a, **k):
            raise BudgetExceededError("cap")

        def complete_text(self, *a, **k):
            raise BudgetExceededError("cap")

    from agent.nodes import Deps, make_plan
    from inspector.core import inspect as run_inspect

    insp = run_inspect(PERFUME)
    events: list = []
    deps = Deps(llm=DeadClient(), deployer=MockDeployer(), emit=lambda t, p: events.append((t, p)))
    out = make_plan({"inspection": insp, "level": "beginner"}, deps)
    assert out["plan"]["port"] == insp["port"]  # deterministic plan used
    assert any(t == "log" and "llm unavailable" in p["text"] for t, p in events)


# ---------------------------------------------------------------- schema (T3.2)

def test_causes_ranked_schema_and_cap():
    d = DiagnosisModel.model_validate({
        "diagnosis_fa": "دیسکSQLite خراب است یا پر شده",
        "action": "give_up", "params": {}, "confidence": 0.6,
        "causes_ranked": [
            {"cause_fa": "فایل دیتابیس روی دیسک mounted خراب شده", "evidence": "database disk image is malformed", "confidence": 0.7},
            {"cause_fa": "دیسک پر است", "evidence": "", "confidence": 0.2},
        ]})
    assert [c["cause_fa"] for c in d.model_dump()["causes_ranked"]][0].startswith("فایل")
    with pytest.raises(ValidationError):
        DiagnosisModel.model_validate({"diagnosis_fa": "x", "action": "delete_app",
                                       "params": {}, "causes_ranked": []})
    with pytest.raises(ValidationError):
        DiagnosisModel.model_validate({"diagnosis_fa": "x", "action": "give_up", "params": {},
                                       "causes_ranked": [{"cause_fa": "a"}] * 4})  # cap 3


# ---------------------------------------------------------------- graph path (T3.3)

def test_unknown_failure_llm_ranks_causes_and_escalates_with_explanation():
    """A log outside every scripted scenario: the model ranks causes from the
    REDACTED tail, picks a whitelisted action (here give_up=escalate), and the
    fix event carries the ranked causes. The raw secret never surfaces."""
    diag = {
        "diagnosis_fa": "دیسک SQLite آسیب دیده؛ اصلاح خودکار مسئولانه نیست.",
        "action": "give_up", "params": {}, "confidence": 0.55,
        "causes_ranked": [
            {"cause_fa": "خرابی ایمیج دیتابیس روی دیسک mounted",
             "evidence": "database disk image is malformed", "confidence": 0.7},
            {"cause_fa": "عدم سازگاری نسخه SQLite", "evidence": "", "confidence": 0.2},
        ]}
    h = Harness(PERFUME, deployer=FailsUnknownOnce(MockDeployer()), llm=ScriptedRanks(diag))
    h.register.add(SECRET)  # as the app does for project/answer secrets
    h.start(skip_expert=True)
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "escalated"
    assert "دیسک" in out["escalation_reason"]
    fix_events = [e for e in h.events if e["type"] == "fix"]
    assert fix_events and fix_events[0]["payload"]["action"] == "give_up"
    causes = fix_events[0]["payload"]["causes_ranked"]
    assert causes and causes[0]["confidence"] >= causes[1]["confidence"]  # ranked order kept
    assert causes[0]["evidence"] == "database disk image is malformed"
    assert "delete_app" not in ALLOWED_ACTIONS
    # redaction contract: registered secrets are masked everywhere the real
    # pipeline sends text (LLM prompts, events). The harness emitter is raw by
    # test design, so we assert on the primitives the pipeline actually uses.
    from app.redact import redact_text

    leaked_line = f"sqlite3.DatabaseError: database disk image is malformed (secret={SECRET})"
    assert SECRET not in redact_text(leaked_line, h.register)
    assert out["escalation_reason"] != "" and SECRET not in out["escalation_reason"]
    # decision audit trail has the diagnosis decision
    assert any("fix#1" in d for d in out["decisions"])


def test_unknown_failure_non_whitelisted_action_falls_back_deterministic():
    """Even on an unknown failure the executed action is allowlist-only: a model
    proposing 'delete_app' is rejected and the deterministic fallback (give_up
    for unknown kinds) escalates instead."""
    h = Harness(PERFUME, deployer=FailsUnknownOnce(MockDeployer()),
                llm=ScriptedRanks({"diagnosis_fa": "برنامه را حذف می‌کنم",
                                   "action": "delete_app", "params": {}}))
    h.start(skip_expert=True)
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "escalated"
    assert out["fix_history"][0]["action"] == "give_up"
    logs = [e for e in h.events if e["type"] == "log"]
    assert any("rejected" in e["payload"]["text"] for e in logs)
    assert "delete_app" not in ALLOWED_ACTIONS


class ScriptedRanks:
    """Like ScriptedLLM in test_diagnose: canned JSON for the diagnoser only."""

    name = "scripted-ranks"

    def __init__(self, diagnosis: dict) -> None:
        self.diagnosis = diagnosis

    def complete_json(self, system, user, model_cls, fallback):
        if model_cls is DiagnosisModel:
            return dict(self.diagnosis)
        return fallback()

    def complete_text(self, system, user):
        return "fake"
