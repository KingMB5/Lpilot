"""FastAPI app — serves the static Persian UI and the JSON/SSE API (contract v1).

Run: uvicorn app.main:app  (Phase 1 uses MockDeployer + FakeLLM everywhere).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import zipfile
import pathlib

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent.assessment import assess, load as load_assessment
from app.config import load_defaults
from app.runner import ensure_llm, load_concepts, resume as runner_resume, start as runner_start
from app.sessions import STORE, Session
from app.zipsafe import ZipSafetyError, extract_zip, project_root_of
from llm.ask import answer_question

ROOT = pathlib.Path(__file__).resolve().parent.parent
UPLOAD_ROOT = ROOT / "data" / "uploads"
FIXTURES = {
    "next-prisma-sqlite": ROOT / "fixtures" / "next-prisma-sqlite",
    "node-express": ROOT / "fixtures" / "node-express",
    "plain-html": ROOT / "fixtures" / "plain-html",
    "broken-project": ROOT / "fixtures" / "broken-project",
}

app = FastAPI(title="LiaraPilot", docs_url=None, redoc_url=None)


def _expired() -> HTTPException:
    return HTTPException(status_code=404, detail="session_expired")


def _get_session(sid: str) -> Session:
    s = STORE.get(sid)
    if s is None:
        raise _expired()
    return s


# ---------------------------------------------------------------- health & data

@app.get("/api/health")
def health():
    return {"ok": True, "demo_mode": os.getenv("DEMO_MODE", "") == "1"}


@app.get("/api/assessment")
def assessment():
    data = load_assessment()
    return {
        "questions": [
            {"id": q["id"], "text": q["text"],
             "options": [{"value": o["value"], "label": o["label"]} for o in q["options"]]}
            for q in data["questions"]
        ],
        "expert_skip": data["expert_skip"],
    }


# ---------------------------------------------------------------- sessions

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    cap_mb = int(load_defaults().get("limits", {}).get("max_upload_mb", 25))
    size = 0
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > cap_mb * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"zip larger than {cap_mb}MB")
            tmp.write(chunk)
        tmp.close()
        dest = UPLOAD_ROOT / tempfile.mkdtemp(prefix="up-", dir=str(UPLOAD_ROOT)) if UPLOAD_ROOT.exists() else UPLOAD_ROOT / "pending"
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_ROOT / tempfile.mkdtemp(prefix="up-", dir=str(UPLOAD_ROOT))
        extract_zip(tmp.name, str(dest))
        root = project_root_of(str(dest))
        s = STORE.create(root, demo=os.getenv("DEMO_MODE", "") == "1")
        return {"session_id": s.id, "demo": s.demo}
    except ZipSafetyError as e:
        raise HTTPException(status_code=400, detail=f"unsafe zip: {e}") from e
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail="file is not a valid zip") from e
    finally:
        os.unlink(tmp.name)


@app.post("/api/sessions/demo")
def demo_session(body: dict):
    key = str(body.get("fixture", ""))
    src = FIXTURES.get(key)
    if src is None or not src.exists():
        raise HTTPException(status_code=400, detail="unknown fixture")
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_ROOT / tempfile.mkdtemp(prefix="fix-", dir=str(UPLOAD_ROOT)) / key
    shutil.copytree(src, dest)
    s = STORE.create(str(dest), demo=True)
    return {"session_id": s.id, "demo": True}


@app.get("/api/sessions/{sid}")
def snapshot(sid: str):
    return _get_session(sid).snapshot()


@app.post("/api/sessions/{sid}/answers")
def answers(sid: str, body: dict):
    s = _get_session(sid)
    try:
        res = assess(body.get("answers", {}), bool(body.get("skip_expert", False)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    s.answers = body.get("answers", {})
    s.skip_expert = bool(body.get("skip_expert", False))
    s.level = res["level"]
    data = load_assessment()
    return {"level": res["level"], "total": res["total"], "max_total": res["max_total"],
            "message_fa": data["scoring"][f"{res['level']}_fa"]}


@app.post("/api/sessions/{sid}/start")
def start(sid: str):
    s = _get_session(sid)
    if s.level is None:
        raise HTTPException(status_code=409, detail="answer the assessment first")
    if not runner_start(s):
        raise HTTPException(status_code=409, detail="session already started")
    return JSONResponse({"ok": True, "demo": s.demo}, status_code=202)


@app.post("/api/sessions/{sid}/approve")
def approve(sid: str, body: dict):
    s = _get_session(sid)
    payload = {
        "approved": bool(body.get("approved", False)),
        "cost_ok": bool(body.get("cost_ok", False)),
        "env_values": dict(body.get("env_values", {})),
        "user_reply": str(body.get("user_reply", "")),  # answer to an ask_user interrupt (T14)
    }
    token = str(body.get("liara_token") or "").strip()
    if token:
        # RAM only (AGENTS.md); registered as a secret so it can never leak into
        # events, LLM prompts or logs. Never echoed back in the response.
        s.liara_token = token
        if s.register is not None:
            s.register.add(token)
    if not runner_resume(s, payload):
        raise HTTPException(status_code=409, detail=f"cannot resume from status={s.status}")
    return JSONResponse({"ok": True}, status_code=202)


@app.post("/api/sessions/{sid}/level")
def set_level(sid: str, body: dict):
    s = _get_session(sid)
    lvl = body.get("level")
    if lvl not in ("beginner", "expert"):
        raise HTTPException(status_code=400, detail="level must be beginner|expert")
    s.level = lvl
    return {"ok": True, "level": lvl}


@app.post("/api/sessions/{sid}/cancel")
def cancel(sid: str):
    s = _get_session(sid)
    if s.status in ("done", "escalated", "cancelled", "error"):
        return {"ok": True, "status": s.status}
    s.cancel_flag = True
    if s.status == "awaiting_approval":
        s.status = "cancelled"
        s.add_event("cancelled", {})
    return JSONResponse({"ok": True}, status_code=202)


# ---------------------------------------------------------------- mid-run Q&A (T15)

@app.post("/api/sessions/{sid}/ask")
def ask(sid: str, body: dict):
    """Free question -> ONE LLM call grounded in glossary + state, redacted.
    Works in any session phase (before/during/after the run)."""
    s = _get_session(sid)
    question = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is empty")
    if len(question) > 500:
        raise HTTPException(status_code=400, detail="question too long (max 500 chars)")
    with s.ask_lock:  # serialize: one LLM call per question, never parallel
        ensure_llm(s)
        answer = answer_question(s.llm, s.register, s.level or "beginner",
                                 question, _state_summary(s), load_concepts())
    return {"answer_fa": answer}


def _state_summary(s: Session) -> dict:
    plan = s.plan or {}
    attempts = 0
    try:
        if s.graph is not None and s.thread_cfg:
            attempts = int((s.graph.get_state(s.thread_cfg).values or {}).get("fix_attempts", 0))
    except Exception:
        attempts = 0
    return {
        "status": s.status,
        "stage_fa": s.stage_fa,
        "level": s.level,
        "plan": {"port": plan.get("port"), "platform": plan.get("platform"),
                 "env_needed": list(plan.get("env_needed") or []),
                 "disks": [d.get("name") for d in (plan.get("disks") or [])]},
        "fix_attempts": attempts,
        "url": (s.report or {}).get("url"),
        "deployer": getattr(s.deployer, "name", None),
        "llm": s.llm_name,
    }


# ---------------------------------------------------------------- SSE

@app.get("/api/events/{sid}")
async def events(sid: str, request: Request):
    s = _get_session(sid)
    last = 0
    lei = request.headers.get("last-event-id")
    if lei and lei.isdigit():
        last = int(lei)

    async def gen():
        pos = last
        idle = 0.0
        while True:
            for e in s.events_after(pos):
                body = {"type": e["type"], **e["payload"]}
                yield f"id: {e['i']}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
                pos = e["i"]
                if e["type"] in ("done", "cancelled"):
                    return
            if pos >= s.seq() and s.terminal():
                return  # reconnect after the run ended: nothing left to replay
            await asyncio.sleep(0.25)
            idle += 0.25
            if idle >= 15:
                yield ": hb\n\n"
                idle = 0.0

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- static UI (last)

app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")
