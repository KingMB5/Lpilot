"""Graph runner: builds session deps (LLM, deployer, redacting emitter) and runs
the LangGraph on a worker thread; handles interrupt -> approval_required and
resume -> terminal states. Any failure lands in status=error with a clean event."""

from __future__ import annotations

import threading
import traceback

from agent.graph import build_graph
from agent.nodes import Deps
from app.config import load_defaults
from app.redact import SecretRegister, redact_obj
from app.sessions import Session
from inspector.core import scan_project
from llm.client import RunBudget, get_client
from llm.explain import explain as explain_text  # noqa: F401  (used by nodes)

STAGE_FA = {
    "assess": "سنجش سطح",
    "inspect": "بازرسی پروژه",
    "explain_risks": "تشریح ریسک‌ها",
    "make_plan": "ساخت برنامه استقرار",
    "prepare_files": "آماده‌سازی فایل‌ها",
    "deploy": "استقرار",
    "read_logs": "خواندن لاگ‌ها",
    "fix": "اصلاح خودکار",
    "verify": "بررسی آدرس زنده",
    "report": "گزارش نهایی",
    "teach_summary": "جمع‌بندی یادگیری",
    "escalate": "نیاز به تصمیم تو",
}


def ensure_llm(session: Session) -> None:
    """Build register/budget/LLM once; used by the graph runner AND the Q&A
    endpoint (T15) so questions work even before a run starts."""
    demo = session.demo or _demo_mode_env()
    concepts = _concepts()
    register = session.register or SecretRegister()
    if session.register is None:
        session.register = register
        _collect_secrets(session, register)
    if session.liara_token:
        register.add(session.liara_token)  # token redacted everywhere, forever
    budget = session.budget or RunBudget(int(load_defaults().get("llm", {}).get("max_tokens_per_run", 60000)))
    if session.budget is None:
        session.budget = budget
    if session.llm is None:
        client = get_client(register, budget, demo_mode=demo, concepts=concepts)
        session.llm = client
        session.llm_name = client.name
        session.demo = demo or client.name.startswith(("fake", "cached(fake)"))


def build_deps(session: Session) -> Deps:
    concepts = _concepts()
    ensure_llm(session)
    if session.deployer is None:
        from deployers import make_deployer

        session.deployer = make_deployer(session)  # DEPLOYER=mock|liara_api|liara_cli (T13)

    def emit(etype: str, payload: dict) -> None:
        safe = redact_obj(payload, session.register)
        if etype == "stage":
            session.stage_fa = payload.get("fa", session.stage_fa)
        session.add_event(etype, safe)
        if etype in ("plan", "report"):
            with session._cond:
                if etype == "plan":
                    session.plan = safe
                else:
                    session.report = safe

    def is_cancelled() -> bool:
        return session.cancel_flag

    return Deps(llm=session.llm, deployer=session.deployer, emit=emit,
                is_cancelled=is_cancelled, concepts=concepts)


def _collect_secrets(session: Session, register: SecretRegister) -> None:
    scan = scan_project(session.project_dir)
    register.add_from_env_example(scan.env_example)
    register.add_from_texts(scan.texts)


def _run(session: Session, resume_with: dict | None) -> None:
    if not session.acquire_run():
        return
    try:
        if session.graph is None:
            session.graph = build_graph(build_deps(session))
            session.thread_cfg = {"configurable": {"thread_id": session.id}}
        if resume_with is None:
            out = session.graph.invoke(
                {"project_dir": session.project_dir,
                 "answers": session.answers, "skip_expert": session.skip_expert},
                session.thread_cfg,
            )
        else:
            from agent.graph import resume as resume_graph

            out = resume_graph(session.graph, session.thread_cfg, resume_with)

        if "__interrupt__" in out:
            payload = out["__interrupt__"][0].value
            session.approval = payload
            session.status = "awaiting_approval"
            session.add_event("approval_required", redact_obj(payload, session.register))
            return

        state = session.graph.get_state(session.thread_cfg).values
        session.status = state.get("status", "done")
        session.plan = state.get("plan", session.plan)
        session.report = state.get("report", session.report)
        if state.get("level") and session.level is None:
            session.level = state["level"]
        session.add_event("done", {"status": session.status})
    except Exception as e:  # noqa: BLE001 — one clean failure path for the UI
        session.status = "error"
        session.error = f"{type(e).__name__}: {e}"
        session.add_event("error", {"message": "خطای داخلی در اجرای ایجنت؛ لاگ سرور را ببین."})
        traceback.print_exc()
    finally:
        session.release_run()


def start(session: Session) -> bool:
    if session.status != "created":
        return False
    session.status = "running"
    t = threading.Thread(target=_run, args=(session, None), daemon=True, name=f"run-{session.id}")
    session.thread = t
    t.start()
    return True


def resume(session: Session, approvals: dict) -> bool:
    if session.status != "awaiting_approval":
        return False
    session.approval = None
    session.status = "running"
    t = threading.Thread(target=_run, args=(session, approvals), daemon=True, name=f"resume-{session.id}")
    session.thread = t
    t.start()
    return True


def _demo_mode_env() -> bool:
    import os

    return os.getenv("DEMO_MODE", "") == "1"


def _concepts() -> dict:
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    data = json.loads((root / "data" / "concepts.json").read_text(encoding="utf-8"))
    return {c["key"]: c for c in data["concepts"]}


# public alias — the Q&A endpoint (T15) loads the glossary through this
load_concepts = _concepts
