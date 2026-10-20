"""T14 — agentic fix loop: allowlist validation, deterministic fallback, ask_user
pause, give_up escalation, MockDeployer's two new canned failures, prompt redaction."""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from agent.graph import resume
from deployers.mock import MockDeployer
from llm.client import RunBudget, _Base, _validate_with_retry
from llm.diagnose import (ALLOWED_ACTIONS, DiagnosisModel, apply_action,
                          deterministic_diagnosis, validate_params)

from test_graph import BROKEN, FULL_ENV, PERFUME, Harness

SECRET_LOG = "super-secret-log-value"


class ScriptedLLM:
    """LLM stub that returns one canned diagnosis for the DIAGNOSER only; any other
    JSON request (e.g. plan building) falls back deterministically, like a real
    client would after validation failures."""

    name = "scripted"

    def __init__(self, diagnosis: dict) -> None:
        self.diagnosis = diagnosis
        self.calls = 0

    def complete_json(self, system, user, model_cls, fallback):
        self.calls += 1
        if model_cls is DiagnosisModel:
            return dict(self.diagnosis)
        return fallback()

    def complete_text(self, system, user):
        return "fake"


class CapturingClient(_Base):
    """Records the REDACTED prompt exactly as a real client would prepare it.
    Only the DIAGNOSER gets the canned JSON; other JSON tasks fall back
    deterministically (a real model would answer those differently)."""

    name = "capturing"

    def __init__(self, register, budget, diagnosis: dict) -> None:
        super().__init__(register, budget)
        self.captured: list[tuple[str, str]] = []
        self._diagnosis = diagnosis

    def _raw_text(self, system, user):
        s, u = self._prepare(system, user)
        self.captured.append((s, u))
        return json.dumps(self._diagnosis, ensure_ascii=False)

    def complete_text(self, system, user):
        return self._raw_text(system, user)[1]

    def complete_json(self, system, user, model_cls, fallback):
        if model_cls is DiagnosisModel:
            return _validate_with_retry(self, system, user, model_cls, fallback)
        return fallback()


class LeakyBadPort:
    """Fails once with bad_port and a log line that contains a secret value;
    logs() mirrors that failure until the inner deployer takes over."""

    name = "leaky"

    def __init__(self, inner) -> None:
        self.inner, self.calls = inner, 0
        self._first_logs = [f"error: auth failed password={SECRET_LOG}"]

    def deploy(self, config):
        self.calls += 1
        if self.calls == 1:
            return {"ok": False, "kind": "bad_port", "message": "invalid port: None",
                    "logs": list(self._first_logs)}
        return self.inner.deploy(config)

    def logs(self):
        return list(self._first_logs) if self.calls == 1 else self.inner.logs()

    def status(self):
        return self.inner.status()


class FailsPortOnce:
    name = "flaky-port"

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def deploy(self, config):
        self.calls += 1
        if self.calls == 1:
            return {"ok": False, "kind": "bad_port", "message": "invalid port: None", "logs": ["error: bad port"]}
        return self.inner.deploy(config)

    def logs(self):
        return self.inner.logs()

    def status(self):
        return self.inner.status()


# ---------------------------------------------------------------- schema / validators

def test_diagnosis_model_rejects_non_allowlisted_action():
    with pytest.raises(ValidationError):
        DiagnosisModel.model_validate({"diagnosis_fa": "x", "action": "delete_everything", "params": {}})
    ok = DiagnosisModel.model_validate({"diagnosis_fa": "پورت عوض می‌شود", "action": "change_port",
                                        "params": {"port": 3000}, "confidence": 0.9})
    assert ok.action in ALLOWED_ACTIONS


def test_validate_params_rules():
    state = {"plan": {}, "inspection": {}}
    # secret values must never come from the model
    ok, err = validate_params("set_env", {"key": "ADMIN_PASSWORD", "value": "hacked-123"}, state)
    assert not ok and "ask_user" in err
    # non-secret env or empty value is fine
    ok, _ = validate_params("set_env", {"key": "PORT", "value": "3000"}, state)
    assert ok
    ok, _ = validate_params("set_env", {"key": "ADMIN_PASSWORD", "value": ""}, state)
    assert ok  # empty placeholder is allowed
    # port range
    assert not validate_params("change_port", {"port": 0}, state)[0]
    assert not validate_params("change_port", {"port": 70000}, state)[0]
    assert not validate_params("change_port", {"port": "abc"}, state)[0]
    # platform allowlist comes from verified liara.json values
    ok, err = validate_params("change_platform", {"platform": "heroku"}, state)
    assert not ok and "liara.json" in err
    assert validate_params("change_platform", {"platform": "next"}, state)[0]
    # ask_user needs a question
    assert not validate_params("ask_user", {}, state)[0]
    # unknown action
    assert not validate_params("deploy_to_mars", {}, state)[0]


def test_deterministic_diagnoses_are_deterministic():
    state = {"plan": {"env_needed": ["MISSING_TOKEN"]}, "approvals": {"env_values": {}},
             "inspection": {}, "project_dir": "/x"}
    for kind in ("bad_port", "missing_env_var", "wrong_start_script", "missing_disk_sqlite", "unknown-x"):
        a = deterministic_diagnosis(kind, state)
        b = deterministic_diagnosis(kind, state)
        assert a == b and a["action"] in ALLOWED_ACTIONS


# ---------------------------------------------------------------- graph-level

def test_invalid_action_rejected_deterministic_fallback_applied():
    h = Harness(PERFUME, deployer=FailsPortOnce(MockDeployer()),
                llm=ScriptedLLM({"diagnosis_fa": "می‌خواهم همه‌چیز را پاک کنم",
                                 "action": "delete_everything", "params": {}}))
    h.start(skip_expert=True)
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "done"                       # fallback (change_port) saved the run
    assert out["plan"]["port"] == 3000
    assert out["fix_history"][0]["action"] == "change_port"
    logs = [e for e in h.events if e["type"] == "log"]
    assert any("rejected" in e["payload"]["text"] for e in logs)


def test_set_env_with_secret_value_rejected_and_never_leaks():
    h = Harness(PERFUME, deployer=FailsPortOnce(MockDeployer()),
                llm=ScriptedLLM({"diagnosis_fa": "رمز را خودم می‌گذارم",
                                 "action": "set_env",
                                 "params": {"key": "ADMIN_PASSWORD", "value": "hacked-123"}}))
    h.start(skip_expert=True)
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "done"                       # deterministic fallback applied
    assert "hacked-123" not in json.dumps(h.events, ensure_ascii=False)
    assert out["plan"]["port"] == 3000                   # fallback was change_port, not set_env


def test_ask_user_pauses_for_human_and_reply_fills_env():
    """ask_user pauses the graph for the human; replies fill the missing env keys
    (one per attempt) and each reply is treated as secret-capable (never emitted)."""
    h = Harness(BROKEN)
    out = h.start(skip_expert=True)
    out = resume(h.graph, h.cfg, {"approved": True, "cost_ok": True, "env_values": {}})
    assert "__interrupt__" in out
    q = out["__interrupt__"][0].value
    assert q["kind"] == "question"
    assert "MISSING_TOKEN" in q["question_fa"]
    # reply 1 -> MISSING_TOKEN filled; the agent then asks for the remaining key
    out = resume(h.graph, h.cfg, {"user_reply": "my-token-value-99"})
    assert "__interrupt__" in out
    q2 = out["__interrupt__"][0].value
    assert q2["kind"] == "question" and "PORT" in q2["question_fa"]
    assert "MISSING_TOKEN" not in q2["question_fa"]   # earlier fix counts as filled
    # reply 2 -> PORT filled -> deploy succeeds
    out = resume(h.graph, h.cfg, {"user_reply": "3000"})
    assert out["status"] == "done", h.state().get("deploy_result")
    actions = [e["action"] for e in out["fix_history"]]
    assert actions == ["set_env", "set_env"]
    # the user's replies never appear in any event
    assert "my-token-value-99" not in json.dumps(h.events, ensure_ascii=False)


def test_change_start_command_applied_and_liara_json_regenerated():
    h = Harness(PERFUME, deployer=FailsPortOnce(MockDeployer()),
                llm=ScriptedLLM({"diagnosis_fa": "اسکریپت شروع اشتباه است",
                                 "action": "change_start_command",
                                 "params": {"start_command": "npm run serve"}}))
    h.start(skip_expert=True)
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "done"
    assert out["plan"]["start_command"] == "npm run serve"
    assert out["fix_history"][0]["action"] == "change_start_command"
    liara = next(f for f in out["plan"]["prepared_files"] if f["name"] == "liara.json")
    assert json.loads(liara["content"])["platform"] == "next"   # regenerated, schema intact


def test_give_up_escalates_immediately_with_reason():
    h = Harness(PERFUME, deployer=FailsPortOnce(MockDeployer()),
                llm=ScriptedLLM({"diagnosis_fa": "مشکل بیرون از اختیار من است",
                                 "action": "give_up", "params": {}}))
    h.start(skip_expert=True)
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "escalated"
    assert out["fix_attempts"] == 1
    assert out["report"]["reason"] == "مشکل بیرون از اختیار من است"
    assert out["fix_history"][-1]["action"] == "give_up"


def test_log_tail_redacted_before_reaching_the_model():
    reg = _register_with_secret()
    llm = CapturingClient(reg, RunBudget(60000),
                          {"diagnosis_fa": "پورت را اصلاح می‌کنم", "action": "change_port",
                           "params": {"port": 3000}, "confidence": 0.9})
    h = Harness(PERFUME, deployer=LeakyBadPort(MockDeployer()), llm=llm, register=reg)
    h.start(skip_expert=True)
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "done"
    assert llm.captured, "model never saw a prompt"
    blob = "\n".join(u for _s, u in llm.captured)
    assert SECRET_LOG not in blob                      # the leaked value never reached the model
    assert "***REDACTED***" in blob                    # ...it was masked in the diagnose prompt
    assert any("diagnose_deploy_failure" in u for _s, u in llm.captured)


def _register_with_secret():
    from app.redact import SecretRegister

    reg = SecretRegister()
    reg.add(SECRET_LOG)
    return reg


# ---------------------------------------------------------------- MockDeployer v2

def test_mock_wrong_start_script_hint_then_fix_succeeds(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"start": "node missing.js"}}),
                                            encoding="utf-8")
    (tmp_path / "server.js").write_text("console.log('ok')", encoding="utf-8")
    d = MockDeployer()
    base = {"app_name": "a", "platform": "node", "port": 3000, "env": {}, "required_env": [],
            "disks": [], "project_dir": str(tmp_path)}
    r = d.deploy({**base, "start_command": "node missing.js"})
    assert r["ok"] is False and r["kind"] == "wrong_start_script"
    assert any("server.js" in line for line in r["logs"])          # hint for the fixer
    r2 = d.deploy({**base, "start_command": "node server.js"})     # after the fix
    assert r2["ok"] is True and r2["kind"] == "success"


def test_wrong_start_script_without_candidate_asks_user(tmp_path):
    # a project whose ONLY js file is the missing entry -> no candidate -> ask_user
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"start": "node gone.js"}}),
                                            encoding="utf-8")
    state = {"plan": {}, "inspection": {"start_command": "node gone.js"},
             "project_dir": str(tmp_path)}
    d = deterministic_diagnosis("wrong_start_script", state)
    assert d["action"] == "ask_user"   # no alternative entry file exists


def test_mock_missing_disk_sqlite_then_add_disk_fix():
    d = MockDeployer()
    base = {"app_name": "a", "platform": "node", "port": 3000,
            "env": {"DATABASE_URL": "file:./db/custom.db"}, "required_env": [],
            "start_command": "npm start", "disks": [], "project_dir": str(PERFUME)}
    r = d.deploy(base)
    assert r["ok"] is False and r["kind"] == "missing_disk_sqlite"
    assert any("disk" in line for line in r["logs"])
    # deterministic diagnosis proposes the documented disk; applying it fixes the deploy
    diag = deterministic_diagnosis("missing_disk_sqlite", {"plan": {}, "inspection": {}})
    assert diag["action"] == "add_disk"
    assert diag["params"]["name"] == "database"
    plan, _desc = apply_action("add_disk", diag["params"], {"plan": {}})
    r2 = d.deploy({**base, "disks": plan["disks"]})
    assert r2["ok"] is True and r2["kind"] == "success"


def test_sqlite_with_disk_already_mounted_deploys():
    d = MockDeployer()
    r = d.deploy({"app_name": "a", "platform": "node", "port": 3000,
                  "env": {"DATABASE_URL": "file:./db/custom.db"}, "required_env": [],
                  "start_command": "npm start", "project_dir": str(PERFUME),
                  "disks": [{"name": "database", "mountTo": "db"}]})
    assert r["ok"] is True
