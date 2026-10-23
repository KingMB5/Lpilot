"""FastAPI app — serves the static Persian UI and the JSON/SSE API (contract v1).

Run: uvicorn app.main:app  (Phase 1 uses MockDeployer + FakeLLM everywhere).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
import time
import zipfile
import pathlib

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent.assessment import assess, load as load_assessment
from agent.risk import RiskDenied, classify, gate, safe_action
from app.config import load_defaults
from app.github import GitHubError, intake as github_intake, parse_repo as github_parse_repo
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

def _lifespan(app: FastAPI):
    """نگهبان پروژه: ترد سراسری زمان‌بندی پایش دوره‌ای (T-care)."""
    from app.care import start_scheduler

    start_scheduler(STORE)
    yield


app = FastAPI(title="LiaraPilot", docs_url=None, redoc_url=None,
              lifespan=_lifespan)


@app.middleware("http")
async def _rate_limit_middleware(request, call_next):
    """A4: protect a public deployment — per-IP + global sliding window.

    Healthcheck is exempt so the platform monitor can always reach it.
    Configure via RATE_LIMIT_PER_MIN / RATE_LIMIT_GLOBAL_M (0 disables).
    """
    from app.ratelimit import check_rate_limit

    resp = check_rate_limit(request.scope)
    if resp is not None:
        return resp
    return await call_next(request)


def _expired() -> HTTPException:
    return HTTPException(status_code=404, detail="session_expired")


def _get_session(sid: str) -> Session:
    s = STORE.get(sid)
    if s is None:
        raise _expired()
    return s


# ---------------------------------------------------------------- health & data

_PROCESS_START = time.time()


@app.get("/api/health")
def health():
    """Healthcheck for the agent's own deployment (T16). No secrets here —
    only booleans about configuration state."""
    from deployers import make_deployer

    return {
        "ok": True,
        "version": app.version,
        "demo_mode": os.getenv("DEMO_MODE", "") == "1",
        "deployer_default": os.getenv("DEPLOYER", "mock"),
        "default_deployer": make_deployer().name if os.getenv("DEMO_MODE", "") != "1" else "mock",
        "llm_configured": bool(os.getenv("LLM_BASE_URL") and os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL")),
        "liara_token_in_env": bool(os.getenv("LIARA_API_TOKEN")),
        "dry_run": os.getenv("LIARA_DRY_RUN", "") == "1",
        "uptime_s": int(time.time() - _PROCESS_START),
    }


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
async def upload(file: UploadFile = File(...)):  # noqa: N803 — multipart field name
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


@app.post("/api/github")
def github(body: dict):
    """دریافت پروژه از گیت‌هاب: tarball ریپو (عمومی یا خصوصی با توکن) دانلود
    و امن استخراج می‌شود؛ بعد مسیر مثل آپلود zip جلو می‌رود.
    توکن فقط از env خوانده می‌شود؛ هرگز در پاسخ/لاگ/رویداد برنمی‌گردد."""
    url = str(body.get("url", ""))
    try:
        owner, repo, _branch = github_parse_repo(url)
        dest = github_intake(url)
    except GitHubError as e:
        raise HTTPException(status_code=e.status, detail=e.message_fa) from e
    s = STORE.create(dest, demo=os.getenv("DEMO_MODE", "") == "1")
    s.add_event("log", {"text": f"repo fetched: {owner}/{repo}"})
    return {"session_id": s.id, "demo": s.demo, "repo": f"{owner}/{repo}"}


# ---------------------------------------------------------------- account (real, read-only)

_ACCT_CACHE: dict = {"ts": 0.0, "data": None}


@app.get("/api/account")
def account():
    """وضعیت واقعی حساب لیارا برای ویجت هدر — فقط GETهای read-only با کش ۵ دقیقه‌ای.
    بدون توکن در پاسخ؛ خطاها فروخورده می‌شوند (ویجت فقط مخفی می‌شود)."""
    token = (os.getenv("LIARA_API_TOKEN", "") or "").strip()
    if not token:
        return {"connected": False}
    now = time.time()
    cached = _ACCT_CACHE.get("data")
    if isinstance(cached, dict) and now - float(_ACCT_CACHE.get("ts", 0.0)) < 300:
        return cached
    out: dict = {"connected": True}
    team = (os.getenv("LIARA_TEAM_ID", "") or "").strip()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(base_url="https://api.liara.ir", timeout=15.0,
                          headers=headers) as c:
            params = {"teamID": team} if team else None
            if team:
                r = c.get("/v1/billing", params=params)
                if r.status_code == 200:
                    # VERIFIED 2026-10-23: with teamID the response carries the
                    # TEAM credit under team.balance (user.balance stays 0).
                    bal = ((r.json() or {}).get("team") or {}).get("balance")
                    if isinstance(bal, (int, float)):
                        out["balance_toman"] = round(float(bal))
            r2 = c.get("/v1/me", params=params)
            if r2.status_code == 200:
                plans = ((r2.json() or {}).get("plans") or {})
                wanted = (os.getenv("LIARA_PLAN_ID", "small-g2") or "").strip()
                projs = plans.get("projects")
                entry = projs.get(wanted) if isinstance(projs, dict) else None
                if isinstance(entry, dict):
                    price = entry.get("price")
                    if isinstance(price, (int, float)):
                        out["default_plan"] = wanted
                        out["plan_price_hourly_toman"] = float(price)
                    dp = entry.get("displayPrice")
                    if isinstance(dp, (int, float)):
                        out["plan_price_monthly_toman"] = float(dp)
    except httpx.HTTPError:
        pass  # شبکه‌ی لیارا در دسترس نیست — ویجت «متصل» نشان داده نمی‌شود
    _ACCT_CACHE["data"] = out
    _ACCT_CACHE["ts"] = now
    return out


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


# ---------------------------------------------------------------- monitoring (T2/T6, read-only)

@app.get("/api/sessions/{sid}/monitor")
def monitor(sid: str):
    """One read-only monitoring sample + the short history + alert state.
    source=liara_api -> values carry an UNVERIFIED badge (units unconfirmed);
    source=inference -> explicitly «استنتاج». This endpoint NEVER mutates
    anything; suggested actions carry their T4 risk tier and still require the
    /ops approval gate to execute."""
    s = _get_session(sid)
    from app.monitor import sample_now

    with s._cond:  # history is mutated by the poller thread
        history = list(s.monitor_history)
        alerting = bool(s.monitor_alerting)
    return {
        "sample": sample_now(s),
        "history": history,
        "alerting": alerting,
        "interval_s": float(os.getenv("MONITOR_INTERVAL_S",
                                      str(load_defaults().get("monitor", {}).get("interval_s", 30)))),
    }


# ---------------------------------------------------------------- post-deploy ops (T4, risk-gated)

OPS_ACTIONS = ("restart",)  # executable post-deploy operations this phase


def _run_op(s, action: str, params: dict) -> dict:
    if action == "restart":
        if not hasattr(s.deployer, "restart"):
            return {"ok": False, "message_fa": "این deployer از ری‌استارت پشتیبانی نمی‌کند."}
        return s.deployer.restart()
    return {"ok": False, "message_fa": "اقدام ناشناخته."}


@app.post("/api/sessions/{sid}/ops")
def ops(sid: str, body: dict):
    """Post-deploy operations behind the three-tier risk gate (T4).
    - tier 1 (auto): read-only — not reachable here (use /monitor), refused.
    - tier 2 (approval): executed ONLY with approved=true for THIS action;
      otherwise 403 approval_required + ops_denied event (visible in UI).
    - tier 3 (forbidden): ALWAYS refused — the agent never deletes apps/disks."""
    s = _get_session(sid)
    action = str(body.get("action", "")).strip()
    approved = bool(body.get("approved", False))
    if action not in OPS_ACTIONS:
        s.add_event("ops_denied", {"action": action or "(empty)",
                                   "reason_fa": "اقدام ناشناخته یا غیرقابل اجرا از این مسیر."})
        raise HTTPException(status_code=400, detail="unknown action")
    tier = classify(action)
    ok, reason = gate(action, approved)
    if not ok:
        s.add_event("ops_denied", {"action": action, "tier": tier, **safe_action(action),
                                   "reason_fa": reason})
        raise HTTPException(status_code=403, detail="approval_required")
    try:
        result = _run_op(s, action, body)
    except RiskDenied:
        raise HTTPException(status_code=403, detail="approval_required") from None
    safe = dict(result)
    safe.pop("logs", None)  # deployer logs travel via deploy_log/monitor, not here
    s.add_event("ops", {"action": action, "tier": tier, **safe_action(action),
                        "ok": bool(result.get("ok")),
                        "message_fa": result.get("message", "")})
    return safe


# ---------------------------------------------------------------- care agent (T-care)

@app.get("/api/care/schedules")
def care_schedules():
    """گزینه‌های زمان‌بندی برای UI — یک منبع حقیقت در سرور."""
    from app.care import SCHEDULE_LABELS

    return {"schedules": SCHEDULE_LABELS}


@app.get("/api/sessions/{sid}/care")
def care_state(sid: str):
    """وضعیت نگهبان + تاریخچهٔ گزارش‌های مراقبت (جدیدترین آخر)."""
    s = _get_session(sid)
    return s.care_summary()


@app.post("/api/sessions/{sid}/care")
def care_schedule(sid: str, body: dict):
    """فعال/غیرفعال‌سازی پایش دوره‌ای (daily|weekly|monthly|off).
    فعال‌سازی فوراً یک پایش اجرا می‌کند (trigger=first) — read-only، سطح ۱."""
    s = _get_session(sid)
    schedule = str(body.get("schedule", "")).strip().lower()
    from app.care import set_schedule

    res = set_schedule(s, schedule)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("message_fa", "bad schedule"))
    return JSONResponse(res, status_code=202)


@app.post("/api/sessions/{sid}/care/run")
def care_run(sid: str):
    """پایش فوری دستی (در ترد پس‌زمینه؛ نتیجه از way of GET /care یا SSE می‌آید)."""
    s = _get_session(sid)
    if not (s.report or {}).get("url"):
        raise HTTPException(status_code=409, detail="no deployed url yet")
    if s.care_running:
        return JSONResponse({"ok": True, "already_running": True}, status_code=202)
    from app.care import run_care_check

    threading.Thread(target=_care_run_bg, args=(s,), daemon=True,
                     name=f"care-manual-{s.id}").start()
    return JSONResponse({"ok": True}, status_code=202)


def _care_run_bg(session) -> None:
    from app.care import run_care_check

    try:
        run_care_check(session, trigger="manual")
    except Exception:  # noqa: BLE001 — پایش هرگز سرور را نمی‌اندازد
        pass


@app.post("/api/sessions/{sid}/care/brief")
def care_brief(sid: str):
    """بریف نگهداری برای ایجنت خودِ کاربر (یک call LLM، کش‌شده در جلسه)."""
    s = _get_session(sid)
    from app.care import build_agent_brief

    with s.ask_lock:  # serialize LLM usage like /ask
        text = build_agent_brief(s)
    return {"brief": text}


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
