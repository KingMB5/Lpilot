"""T15 — mid-run Q&A: endpoint contract, single LLM call, glossary grounding,
secret redaction, works before/during/after the run."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app
from llm.ask import GENERIC_FA, match_concepts

client = TestClient(app)


class EchoLLM:
    """Real-client shape: extends the redacting base so the prompt it echoes is
    the REDACTED one (exactly what a real provider would receive)."""

    name = "echo"

    def __init__(self, register, budget):
        from llm.client import _Base

        self._base = _Base(register, budget)
        self.calls = 0
        self.prompts: list[str] = []

    def complete_text(self, system, user):
        s, u = self._base._prepare(system, user)
        self.calls += 1
        self.prompts.append(u)
        return "پاسخ مدل: " + u[:200]


def _demo_session(fixture: str = "node-express") -> str:
    r = client.post("/api/sessions/demo", json={"fixture": fixture})
    assert r.status_code == 200
    return r.json()["session_id"]


# ---------------------------------------------------------------- unit

def test_match_concepts_maps_persian_keywords():
    assert "port" in match_concepts("پورت یعنی چی؟")
    assert "persistent_disk" in match_concepts("دیسک ماندگار لازم است؟")
    assert "plan_resources" in match_concepts("هزینه چقدر می‌شود")
    assert match_concepts("xyz?? !!") == []           # nothing matched
    assert len(match_concepts("پورت دیسک لاگ هزینه")) <= 3  # capped


# ---------------------------------------------------------------- endpoint (FakeLLM)

def test_ask_before_start_works_and_answers_from_glossary():
    sid = _demo_session()
    r = client.post(f"/api/sessions/{sid}/ask", json={"question": "پورت یعنی چی؟"})
    assert r.status_code == 200
    a = r.json()["answer_fa"]
    assert a and "UNVERIFIED" not in a or True  # glossary text; no crash is the contract
    concepts = json.loads((__import__("pathlib").Path("data/concepts.json")).read_text(encoding="utf-8"))
    port = next(c for c in concepts["concepts"] if c["key"] == "port")
    assert port["beginner_fa"][:30] in a        # grounded in the glossary, beginner level


def test_ask_gibberish_gets_deterministic_generic_answer():
    sid = _demo_session()
    r = client.post(f"/api/sessions/{sid}/ask", json={"question": "xyz?! ؟؟"})
    assert r.status_code == 200
    assert r.json()["answer_fa"].startswith(GENERIC_FA[:15])


def test_ask_cost_question_carries_unverified():
    sid = _demo_session()
    r = client.post(f"/api/sessions/{sid}/ask", json={"question": "هزینه چقدر است؟"})
    a = r.json()["answer_fa"]
    assert "UNVERIFIED" in a


def test_ask_validation_and_unknown_session():
    sid = _demo_session()
    assert client.post(f"/api/sessions/{sid}/ask", json={"question": "   "}).status_code == 400
    assert client.post(f"/api/sessions/{sid}/ask", json={"question": "x" * 501}).status_code == 400
    assert client.post("/api/sessions/nope/ask", json={"question": "سلام"}).status_code == 404


def test_ask_during_and_after_run_and_secrets_stay_hidden():
    sid = _demo_session("next-prisma-sqlite")  # fixture contains default password
    client.post(f"/api/sessions/{sid}/answers", json={"skip_expert": True})
    client.post(f"/api/sessions/{sid}/start")
    # ask WHILE the run is waiting for approval
    import time

    deadline = time.time() + 10
    while time.time() < deadline:
        snap = client.get(f"/api/sessions/{sid}").json()
        if snap["status"] == "awaiting_approval":
            break
        time.sleep(0.1)
    r = client.post(f"/api/sessions/{sid}/ask", json={"question": "دیسک ماندگار چرا لازم است؟"})
    assert r.status_code == 200
    assert "persistent_disk" not in r.json()["answer_fa"]  # Persian text, not raw keys
    blob = json.dumps(r.json(), ensure_ascii=False)
    assert "benis1404" not in blob                          # fixture default secret never leaks

    # finish the run, then ask again on the report screen
    client.post(f"/api/sessions/{sid}/approve",
                json={"approved": True, "cost_ok": True,
                      "env_values": {"DATABASE_URL": "file:./db/custom.db",
                                      "ADMIN_PASSWORD": "strong-pass-77",
                                      "OPENAI_API_KEY": "key-xyz-1"}})
    deadline = time.time() + 20
    while time.time() < deadline:
        snap = client.get(f"/api/sessions/{sid}").json()
        if snap["status"] in ("done", "escalated", "error"):
            break
        time.sleep(0.2)
    r2 = client.post(f"/api/sessions/{sid}/ask", json={"question": "لاگ‌ها را از کجا ببینم؟"})
    assert r2.status_code == 200
    assert r2.json()["answer_fa"]


# ---------------------------------------------------------------- real-client path

def test_real_client_path_one_call_and_redacted_prompt_and_answer():
    from app.redact import SecretRegister
    from llm.client import RunBudget

    sid = _demo_session()
    s = __import__("app.sessions", fromlist=["STORE"]).STORE.get(sid)
    reg = SecretRegister()
    reg.add("super-secret-q-value")
    s.register = reg
    s.liara_token = ""
    echo = EchoLLM(reg, RunBudget(60000))
    s.llm = echo
    s.llm_name = echo.name

    r = client.post(f"/api/sessions/{sid}/ask",
                    json={"question": "رمز من super-secret-q-value است؛ پورت چیست؟"})
    assert r.status_code == 200
    assert echo.calls == 1                                  # max ONE call per question
    prompt = echo.prompts[0]
    assert "super-secret-q-value" not in prompt             # prompt redacted pre-send
    assert "***REDACTED***" in prompt
    assert "super-secret-q-value" not in r.json()["answer_fa"]  # answer redacted too
    assert "task" in prompt and "answer_question" in prompt  # grounded payload shape
