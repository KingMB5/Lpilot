"""Pure-ish LangGraph nodes. All side effects enter through Deps (llm, deployer,
emitter, cancel flag), so nodes stay testable and the graph is cheap to replace.

Deterministic code executes and enforces limits; the LLM only explains/wordsmiths
(AGENTS.md). Env VALUES never enter emitted payloads — redaction happens in the
app emitter, and nodes never place them in events in the first place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.assessment import assess
from agent.risk import safe_action
from agent.plan import PlanModel, build_plan_deterministic, liara_json_from_plan
from deployers.base import make_deploy_config
from llm.diagnose import (DIAGNOSE_SYSTEM, LOG_TAIL_CHARS, DiagnosisModel,
                          _missing_env_keys, apply_action, build_diagnose_context,
                          build_diagnose_user, deterministic_diagnosis, safe_params,
                          validate_params)
from llm.explain import explain as explain_risk_text
from llm.prompts import PLAN_SYSTEM, build_plan_user

MAX_FIX_ATTEMPTS = 3  # enforced in code, never delegated to the LLM


@dataclass
class Deps:
    llm: Any                                    # LLMClient
    deployer: Any                               # Deployer instance for this session
    emit: Callable[[str, dict], None] = field(default=lambda t, p: None)
    is_cancelled: Callable[[], bool] = field(default=lambda: False)
    checker: Callable[[str], dict] | None = None  # url -> {ok, status}
    verify_timeout_s: int = 10
    concepts: dict = field(default_factory=dict)  # glossary key -> entry


def _decide(state: dict, update: dict, key: str) -> dict:
    """Merge a node update with an appended audit decision (never drop fields)."""
    out = dict(update)
    out["decisions"] = list(state.get("decisions", [])) + [key]
    return out


def _cancelled(state: dict, deps: Deps) -> bool:
    return bool(state.get("cancel_requested")) or deps.is_cancelled()


# ---------------------------------------------------------------- nodes

def assess_level(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    deps.emit("stage", {"name": "assess", "fa": "سنجش سطح"})
    if state.get("answers") or state.get("skip_expert"):
        res = assess(state.get("answers", {}), bool(state.get("skip_expert")))
        level = res["level"]
        deps.emit("log", {"text": f"level={level} total={res['total']}/{res['max_total']} reason={res['reason']}"})
        return {"level": level, "status": "running"}
    return {"level": state.get("level", "beginner"), "status": "running"}


def inspect_node(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    deps.emit("stage", {"name": "inspect", "fa": "بازرسی پروژه"})
    from inspector.core import inspect as run_inspect

    result = run_inspect(state["project_dir"])
    deps.emit("log", {"text": f"platform={result['platform']} port={result['port']} risks={len(result['risks'])}"})
    return {"inspection": result, "status": "running"}


def explain_risks(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    deps.emit("stage", {"name": "explain_risks", "fa": "تشریح ریسک‌ها"})
    out: dict[str, dict] = {}
    for risk in state.get("inspection", {}).get("risks", []):
        texts = {
            "beginner_text": explain_risk_text(deps.llm, risk, "beginner", deps.concepts),
            "expert_text": explain_risk_text(deps.llm, risk, "expert", deps.concepts),
        }
        out[risk["id"]] = texts
        deps.emit("risk", {**risk, **texts})
    return {"explanations": out, "status": "running"}


def make_plan(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    deps.emit("stage", {"name": "make_plan", "fa": "ساخت برنامه استقرار"})

    fallback_plan = build_plan_deterministic(state.get("inspection", {}), state.get("level", "beginner"))

    def fallback() -> dict:
        deps.emit("log", {"text": "plan: deterministic fallback used"})
        return fallback_plan

    try:
        plan = deps.llm.complete_json(
            PLAN_SYSTEM, build_plan_user(state.get("inspection", {}), state.get("level", "beginner")),
            PlanModel, fallback,
        )
    except Exception as e:  # budget exceeded / client failure -> deterministic plan
        deps.emit("log", {"text": f"plan: llm unavailable ({type(e).__name__}) — deterministic plan"})
        plan = fallback_plan

    # deterministic overrides: port/env/limits/disks are ours, not the LLM's
    plan["port"] = fallback_plan["port"]
    plan["env_needed"] = fallback_plan["env_needed"]
    plan["platform"] = fallback_plan["platform"]
    plan["prepared_files"] = fallback_plan["prepared_files"]
    plan["disks"] = fallback_plan["disks"]
    plan.setdefault("cost_unverified", True)
    deps.emit("plan", plan)
    return _decide(state, {"plan": plan, "status": "running"}, "make_plan: deterministic fields enforced")


def await_approval(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    from langgraph.types import interrupt

    plan = state.get("plan", {})
    payload = {
        "estimated_cost": plan.get("estimated_cost"),
        "cost_unverified": plan.get("cost_unverified", True),
        "env_needed": plan.get("env_needed", []),
        "prepared_files": plan.get("prepared_files", []),
    }
    # note: the approval_required SSE event is emitted by the app layer when invoke
    # returns __interrupt__ — emitting here would duplicate on resume.
    answer = interrupt(payload)  # graph interrupt — resumes with the approval dict
    approved = bool(answer.get("approved"))
    decision = "approved" if approved else "declined"
    return _decide(
        state,
        {"approvals": answer, "status": "running" if approved else "declined"},
        f"await_approval: {decision}",
    )


def prepare_files(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    deps.emit("stage", {"name": "prepare_files", "fa": "آماده‌سازی فایل‌ها"})
    plan = state.get("plan", {})
    names = [f.get("name") for f in plan.get("prepared_files", [])]
    deps.emit("log", {"text": f"prepared (not yet applied): {', '.join(names)}"})
    return _decide(state, {"status": "running"}, "prepare_files: generated without applying (approval already granted)")


def deploy(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    deps.emit("stage", {"name": "deploy", "fa": "استقرار"})
    deps.emit("log", {"text": f"deployer={getattr(deps.deployer, 'name', '?')}"})
    config = make_deploy_config(state.get("inspection", {}), state.get("plan", {}),
                                state.get("approvals", {}), project_dir=state.get("project_dir"))
    # never leak env VALUES: announce keys only
    deps.emit("log", {"text": "deploy env keys: " + (", ".join(sorted(config["env"])) or "(none)")})
    result = deps.deployer.deploy(config)
    for line in result.get("logs", []):
        deps.emit("deploy_log", {"text": line})
    status = "running" if result.get("ok") else "deploy_failed"
    # every action shows its risk tier in the UI (T4): a deploy is tier 2,
    # already covered by the approval interrupt the user just passed
    return {"deploy_result": result, "logs": result.get("logs", []), "status": status,
            "risk_tier": safe_action("deploy_new_version")}


def read_logs(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    lines = deps.deployer.logs()
    deps.emit("stage", {"name": "read_logs", "fa": "خواندن لاگ‌ها"})
    return {"logs": lines, "status": state.get("status", "running")}


def fix(state: dict, deps: Deps) -> dict:
    """Agentic fix loop (T14): the LLM diagnoses from the redacted log tail,
    deterministic code validates the action against the allowlist, applies it,
    and logs the decision. Max 3 attempts — enforced HERE, never by the model.
    ask_user pauses the graph for the human (LangGraph interrupt)."""
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    attempts = int(state.get("fix_attempts", 0))
    if attempts >= MAX_FIX_ATTEMPTS:
        deps.emit("log", {"text": f"fix cap reached ({MAX_FIX_ATTEMPTS}) — escalating to user"})
        return {"status": "escalated"}

    result = state.get("deploy_result", {})
    kind = result.get("kind")
    log_tail = "\n".join(state.get("logs", []))[-LOG_TAIL_CHARS:]
    ctx = build_diagnose_context(state, kind, log_tail)

    def fallback() -> dict:
        return deterministic_diagnosis(kind, state)

    try:
        diagnosis = deps.llm.complete_json(DIAGNOSE_SYSTEM, build_diagnose_user(ctx),
                                           DiagnosisModel, fallback)
    except Exception as e:  # budget/client failure -> deterministic diagnosis
        deps.emit("log", {"text": f"diagnose: llm unavailable ({type(e).__name__}) — deterministic diagnosis"})
        diagnosis = fallback()

    ok, err = validate_params(diagnosis["action"], diagnosis.get("params") or {}, state)
    if not ok:
        deps.emit("log", {"text": f"diagnosis rejected ({err}) — deterministic fallback applied"})
        diagnosis = fallback()
        if not validate_params(diagnosis["action"], diagnosis.get("params") or {}, state)[0]:
            diagnosis = {"diagnosis_fa": "اصلاح خودکار ممکن نیست.", "action": "give_up",
                         "params": {}, "confidence": 0.0}
    # REAL-evidence override (2026-10-22): for an unhealthy live URL, a start
    # command referencing a file absent from the project is a deterministic
    # cause — it outranks a model guess (checked against the real project dir).
    if kind == "unhealthy_url" and diagnosis.get("action") in ("ask_user", "give_up"):
        det = deterministic_diagnosis(kind, state)
        if det.get("action") == "change_start_command":
            deps.emit("log", {"text": "diagnosis: deterministic evidence overrides model guess (start file missing)"})
            diagnosis = det

    action = diagnosis["action"]
    params = diagnosis.get("params") or {}
    attempts += 1
    history = list(state.get("fix_history", []))
    causes = [{"cause_fa": c.get("cause_fa", ""), "evidence": c.get("evidence", ""),
               "confidence": c.get("confidence", 0)}
              for c in (diagnosis.get("causes_ranked") or [])][:3]  # ranked-cause analysis (T3)

    if action == "give_up":
        reason = diagnosis.get("diagnosis_fa", "ادامه‌ی خودکار مسئولانه نیست.")
        deps.emit("fix", {"attempt": attempts, "max_attempts": MAX_FIX_ATTEMPTS,
                          "action": "give_up", "reason_fa": reason, "causes_ranked": causes})
        return _decide(state, {"fix_attempts": attempts, "status": "escalated",
                               "escalation_reason": reason,
                               "fix_history": history + [{"attempt": attempts, "action": "give_up"}]},
                       f"fix#{attempts}: give_up")

    if action == "ask_user":
        from langgraph.types import interrupt

        answer = interrupt({"kind": "question", "question_fa": str(params.get("question_fa", "")),
                            "attempt": attempts, "max_attempts": MAX_FIX_ATTEMPTS})
        reply = str((answer or {}).get("user_reply", "")).strip()
        missing = _missing_env_keys(state)
        if kind == "missing_env_var" and reply and missing:
            key = missing[0]
            reg = getattr(deps.llm, "register", None)
            if reg is not None:
                reg.add(reply)  # the reply is user input — treat as secret-capable
            plan = json.loads(json.dumps(state.get("plan", {})))
            plan.setdefault("env_fixes", {})[key] = reply
            deps.emit("fix", {"attempt": attempts, "max_attempts": MAX_FIX_ATTEMPTS,
                              "action": "set_env", "params": {"key": key, "value": "***"},
                              "reason_fa": f"مقدار «{key}» از پاسخ تو گرفته و در کانفیگ استقرار گذاشته شد.",
                              "risk_tier": safe_action("set_env"),
                              "risk_note_fa": "مقدار از خودت گرفته شد؛ هرگز به لاگ یا مدل برنمی‌گردد."})
            return _decide(state, {"plan": plan, "fix_attempts": attempts, "status": "running",
                                   "fix_history": history + [{"attempt": attempts,
                                                              "action": "set_env", "params": {"key": key}}]},
                           f"fix#{attempts}: set_env {key} (from user reply)")
        deps.emit("fix", {"attempt": attempts, "max_attempts": MAX_FIX_ATTEMPTS,
                          "action": "ask_user", "params": safe_params("ask_user", params),
                          "reason_fa": diagnosis.get("diagnosis_fa", ""),
                          "causes_ranked": causes,
                          "reply_used": bool(reply)})
        return _decide(state, {"fix_attempts": attempts, "status": "running",
                               "fix_history": history + [{"attempt": attempts, "action": "ask_user",
                                                          "reply_used": bool(reply)}]},
                       f"fix#{attempts}: ask_user (reply {'used' if reply else 'empty'})")

    plan, _desc = apply_action(action, params, state)
    plan["prepared_files"] = [{"name": "liara.json", "content": liara_json_from_plan(plan)}]
    deps.emit("fix", {"attempt": attempts, "max_attempts": MAX_FIX_ATTEMPTS,
                      "action": action, "params": safe_params(action, params),
                      "reason_fa": diagnosis.get("diagnosis_fa", ""),
                      "causes_ranked": causes,
                      # tier 2 — applied inside the run the user already approved;
                      # the UI shows the tier label so the audit trail is explicit
                      "risk_tier": safe_action(action),
                      "risk_note_fa": "اجرا در چارچوب تأیید استقرارِ همین اجرا"})
    return _decide(state, {"plan": plan, "fix_attempts": attempts, "status": "running",
                           "fix_history": history + [{"attempt": attempts, "action": action,
                                                      "params": safe_params(action, params)}]},
                   f"fix#{attempts}: {action} {json.dumps(safe_params(action, params), ensure_ascii=False)}")


def verify(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    deps.emit("stage", {"name": "verify", "fa": "بررسی آدرس زنده"})
    url = (state.get("deploy_result") or {}).get("url")
    if not url:
        return {"status": "deploy_failed"}
    if deps.checker is not None:
        res = deps.checker(url)
    else:
        res = {"ok": True, "status": 200, "simulated": True}  # mock mode default
    res["url"] = url
    deps.emit("verify", res)
    if res.get("ok"):
        return {"status": "running"}
    # REAL evidence (2026-10-22): the release built READY but the live URL does
    # not serve — mark the failure kind so the fix loop can diagnose from it.
    err = res.get("error") or f"http {res.get('status')}"
    return {"status": "deploy_failed",
            "deploy_result": {**(state.get("deploy_result") or {}),
                              "kind": "unhealthy_url",
                              "message": f"live URL check failed: {err}"}}


def report(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    inspection = state.get("inspection", {})
    plan = state.get("plan", {})
    result = state.get("deploy_result", {})
    rep = {
        "status": "deployed",
        "url": result.get("url"),
        "platform": inspection.get("platform"),
        "port": plan.get("port"),
        "risks": inspection.get("risks", []),
        "steps": plan.get("steps", []),
        "prepared_files": plan.get("prepared_files", []),
        "fix_history": state.get("fix_history", []),
        "decisions": state.get("decisions", []),
        "estimated_cost": plan.get("estimated_cost"),
        "cost_unverified": plan.get("cost_unverified", True),
        "level": state.get("level"),
    }
    deps.emit("report", rep)
    return {"report": rep, "status": "done"}


def teach_summary(state: dict, deps: Deps) -> dict:
    deps.emit("stage", {"name": "teach_summary", "fa": "جمع‌بندی یادگیری"})
    learned: list[str] = []
    for risk in state.get("inspection", {}).get("risks", []):
        key = (risk.get("concept_keys") or ["platform"])[0]
        c = deps.concepts.get(key)
        if c and c["beginner_fa"] not in learned:
            learned.append(c["beginner_fa"])
    next_time = [
        "قبل از zip کردن، node_modules و .next و فایل‌های دیتابیس را حذف کن.",
        "مقدار رمزها را فقط در متغیرهای محیطیِ مقصد بگذار.",
        "بعد از هر استقرار، آدرس زنده را با یک درخواست HTTP چک کن.",
        "برای هر مشکل، اول لاگ، بعد فرضیه، بعد اصلاح.",
        "هزینه‌ی هر پلن را قبل از تأیید ببین.",
    ]
    rep = dict(state.get("report") or {})
    rep["learned"] = learned[:5]
    rep["next_time"] = next_time
    deps.emit("report", rep)
    return {"report": rep, "learned": rep["learned"], "next_time": next_time}


def escalate(state: dict, deps: Deps) -> dict:
    deps.emit("stage", {"name": "escalate", "fa": "نیاز به تصمیم تو"})
    reason = state.get("escalation_reason") or "حلقه‌ی خودکار به سقف ۳ تلاش رسید؛ تصمیم با تو است."
    rep = {
        "status": "needs_user_decision",
        "reason": reason,
        "deploy_result": state.get("deploy_result", {}),
        "fix_history": state.get("fix_history", []),
        "logs": state.get("logs", []),
        "plan": state.get("plan", {}),
        "risks": state.get("inspection", {}).get("risks", []),
        "decisions": state.get("decisions", []),
        "level": state.get("level"),
    }
    deps.emit("report", rep)
    return {"report": rep, "status": "escalated"}


def cancelled(state: dict, deps: Deps) -> dict:
    deps.emit("cancelled", {})
    return {"status": "cancelled"}
