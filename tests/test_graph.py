"""T7 — LangGraph transitions, approval gate, fix-loop cap, cancel, secret hygiene."""

from __future__ import annotations

import json
import pathlib


from agent.graph import build_graph, resume
from agent.nodes import Deps
from app.redact import SecretRegister, redact_obj
from deployers.mock import MockDeployer
from llm.client import FakeLLM, RunBudget

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
CONCEPTS = {c["key"]: c for c in json.loads((ROOT / "data" / "concepts.json").read_text(encoding="utf-8"))["concepts"]}

PERFUME = str(FIXTURES / "next-prisma-sqlite")
BROKEN = str(FIXTURES / "broken-project")

FULL_ENV = {"DATABASE_URL": "file:./db/custom.db", "ADMIN_PASSWORD": "strong-pass-77", "OPENAI_API_KEY": "key-xyz-1"}


class Harness:
    def __init__(self, project_dir: str, deployer: MockDeployer | None = None,
                 register: SecretRegister | None = None, is_cancelled=lambda: False,
                 llm: FakeLLM | None = None):
        self.project_dir = project_dir
        self.register = register or SecretRegister()
        self.deployer = deployer or MockDeployer()
        self.events: list[dict] = []

        def emit(t, p):
            self.events.append({"type": t, "payload": redact_obj(p, self.register)})

        self.deps = Deps(
            llm=llm or FakeLLM(self.register, RunBudget(60000), CONCEPTS),
            concepts=CONCEPTS,
            deployer=self.deployer,
            emit=emit,
            is_cancelled=is_cancelled,
        )
        self.graph = build_graph(self.deps)
        self.cfg = {"configurable": {"thread_id": "t-test"}}

    def start(self, **initial):
        return self.graph.invoke({"project_dir": self.project_dir, **initial}, self.cfg)

    def resume(self, approvals: dict):
        return resume(self.graph, self.cfg, approvals)

    def state(self) -> dict:
        return self.graph.get_state(self.cfg).values


def test_approval_gate_blocks_deploy_until_resume():
    h = Harness(PERFUME)
    out = h.start(skip_expert=True)
    assert "__interrupt__" in out
    payload = out["__interrupt__"][0].value
    assert payload["cost_unverified"] is True
    assert "liara.json" in [f["name"] for f in payload["prepared_files"]]
    assert h.state().get("deploy_result") is None        # nothing deployed yet
    assert len(h.deployer._logs) == 0                     # deployer untouched


def test_expert_happy_path_done_without_teach_summary():
    h = Harness(PERFUME)
    h.start(skip_expert=True)
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "done"
    assert out["report"]["status"] == "deployed"
    assert out["report"]["url"].startswith("https://")
    assert "learned" not in out["report"]                 # expert: no teaching
    disks = json.loads(out["plan"]["prepared_files"][0]["content"]).get("disks")
    assert disks == [{"name": "database", "mountTo": "db"}]  # R1 -> disk wired


def test_beginner_happy_path_has_teach_summary():
    h = Harness(PERFUME)
    h.start(answers={"q1": "never", "q2": "used", "q3": "know", "q4": "know"})
    out = h.resume({"approved": True, "cost_ok": True, "env_values": FULL_ENV})
    assert out["status"] == "done"
    assert out["level"] == "beginner"
    assert len(out["report"]["learned"]) == 5
    assert len(out["report"]["next_time"]) == 5


def test_declined_approval_ends_clean():
    h = Harness(PERFUME)
    h.start(skip_expert=True)
    out = h.resume({"approved": False})
    assert out["status"] == "cancelled"
    assert h.state().get("deploy_result") is None


def _resume_questions(h, first_payload: dict, reply: str = "", max_resumes: int = 6) -> dict:
    """Resume the pending interrupt; answer ask_user questions with `reply`.
    The FIRST resume delivers the approval payload; subsequent ones answer questions."""
    payload = first_payload
    for _ in range(max_resumes):
        out = resume(h.graph, h.cfg, payload)
        if "__interrupt__" not in out:
            return out
        payload = {"user_reply": reply}  # question interrupt -> answer it
    raise AssertionError("too many interrupts")


def test_fix_loop_cap_escalates_after_three_attempts():
    h = Harness(BROKEN)  # MISSING_TOKEN required, user gives no value -> mock keeps failing
    h.start(skip_expert=True)
    out = _resume_questions(h, {"approved": True, "cost_ok": True, "env_values": {}})
    assert out["status"] == "escalated"
    assert out["report"]["status"] == "needs_user_decision"
    assert out["fix_attempts"] == 3
    assert len(out["fix_history"]) == 3
    assert "سقف" in out["report"]["reason"]
    assert all(e["action"] == "ask_user" for e in out["fix_history"])


def test_bad_port_fixed_then_success():
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

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td)
        (p / "package.json").write_text(json.dumps({"name": "n", "scripts": {"start": "node s.js"}}), encoding="utf-8")
        (p / "s.js").write_text("console.log('no listen here')", encoding="utf-8")
        h = Harness(str(p), deployer=FailsPortOnce(MockDeployer()))
        h.start(skip_expert=True)
        out = h.resume({"approved": True, "cost_ok": True, "env_values": {}})
        assert out["status"] == "done"
        assert out["plan"]["port"] == 3000
        assert len(out["fix_history"]) == 1
        assert out["fix_history"][0]["action"] == "change_port"
        assert out["fix_history"][0]["params"]["port"] == 3000


def test_mock_deployer_canned_failures():
    d = MockDeployer()
    r = d.deploy({"app_name": "a", "platform": "node", "port": None, "env": {}, "required_env": ["X"]})
    assert r["ok"] is False and r["kind"] == "bad_port"
    r = d.deploy({"app_name": "a", "platform": "node", "port": 3000, "env": {"X": ""}, "required_env": ["X"]})
    assert r["ok"] is False and r["kind"] == "missing_env_var" and "X" in r["message"]
    r = d.deploy({"app_name": "a", "platform": "node", "port": 3000, "env": {"X": "v"}, "required_env": ["X"]})
    assert r["ok"] is True and r["kind"] == "success" and r["url"].startswith("https://")
    assert d.status() == {"healthy": True, "url": r["url"]}
    assert any("listening" in line for line in d.logs())


def test_cancel_before_start():
    h = Harness(PERFUME, is_cancelled=lambda: True)
    out = h.start(skip_expert=True)
    assert out["status"] == "cancelled"
    types = {e["type"] for e in h.events}
    assert "cancelled" in types


def test_no_secrets_in_emitted_events():
    reg = SecretRegister()
    # secrets visible to the session: fixture default + a user-typed value
    reg.add_from_texts({"lib/auth.ts": 'const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "benis1404";'})
    h = Harness(PERFUME, register=reg)
    h.start(answers={"q1": "never", "q2": "used", "q3": "know", "q4": "know"})
    h.resume({"approved": True, "cost_ok": True, "env_values": {**FULL_ENV, "ADMIN_PASSWORD": "top-secret-99"}})
    blob = json.dumps(h.events, ensure_ascii=False)
    assert "benis1404" not in blob
    assert "top-secret-99" not in blob
    assert "strong-pass-77" not in blob
    # but env KEYS are announced (values never)
    key_lines = [e for e in h.events if e["type"] == "log" and "env keys" in e["payload"]["text"]]
    assert key_lines and "ADMIN_PASSWORD" in key_lines[0]["payload"]["text"]


def test_risk_events_carry_both_level_texts():
    h = Harness(PERFUME)
    h.start(skip_expert=True)
    risks = [e for e in h.events if e["type"] == "risk"]
    assert risks
    r1 = next(e for e in risks if e["payload"]["id"] == "R1_sqlite_without_disk")
    assert "دیسک ماندگار" in r1["payload"]["beginner_text"]
    assert "UNVERIFIED" in r1["payload"]["expert_text"]


def test_interrupt_payload_has_no_env_values():
    h = Harness(PERFUME)
    out = h.start(skip_expert=True)
    blob = json.dumps(out["__interrupt__"][0].value)
    assert "benis1404" not in blob
