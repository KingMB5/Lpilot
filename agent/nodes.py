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
from agent.plan import PlanModel, build_plan_deterministic
from deployers.base import make_deploy_config
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

    # deterministic overrides: port/env/limits are ours, not the LLM's
    plan["port"] = fallback_plan["port"]
    plan["env_needed"] = fallback_plan["env_needed"]
    plan["platform"] = fallback_plan["platform"]
    plan["prepared_files"] = fallback_plan["prepared_files"]
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
    config = make_deploy_config(state.get("inspection", {}), state.get("plan", {}), state.get("approvals", {}))
    # never leak env VALUES: announce keys only
    deps.emit("log", {"text": "deploy env keys: " + (", ".join(sorted(config["env"])) or "(none)")})
    result = deps.deployer.deploy(config)
    for line in result.get("logs", []):
        deps.emit("deploy_log", {"text": line})
    status = "running" if result.get("ok") else "deploy_failed"
    return {"deploy_result": result, "logs": result.get("logs", []), "status": status}


def read_logs(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    lines = deps.deployer.logs()
    deps.emit("stage", {"name": "read_logs", "fa": "خواندن لاگ‌ها"})
    return {"logs": lines, "status": state.get("status", "running")}


def fix(state: dict, deps: Deps) -> dict:
    if _cancelled(state, deps):
        return {"status": "cancelled"}
    attempts = int(state.get("fix_attempts", 0))
    if attempts >= MAX_FIX_ATTEMPTS:
        deps.emit("log", {"text": f"fix cap reached ({MAX_FIX_ATTEMPTS}) — escalating to user"})
        return {"status": "escalated"}

    result = state.get("deploy_result", {})
    kind = result.get("kind")
    plan = json.loads(json.dumps(state.get("plan", {})))  # deep copy
    approvals = state.get("approvals", {}) or {}

    if kind == "bad_port":
        plan["port"] = 3000  # documented default for next/node
        action = f"set port 3000 (was {result.get('message')})"
        reason = "پورت نامعتبر بود؛ به پیش‌فرض مستند ۳۰۰۰ برگشتیم."
    elif kind == "missing_env_var":
        missing = [k for k in plan.get("env_needed", []) if not str((approvals.get("env_values") or {}).get(k, "")).strip()]
        filled = [k for k in plan.get("env_needed", []) if str((approvals.get("env_values") or {}).get(k, "")).strip()]
        action = f"merge user-provided values for: {', '.join(filled) or '(none)'}; empty placeholders for: {', '.join(missing) or '(none)'}"
        reason = "متغیرهای محیطی لازم به کانفیگ استقرار وصل شدند (مقادیر خالی نیازمند ورودی تو هستند)."
    else:
        deps.emit("log", {"text": f"no deterministic fix for kind={kind}"})
        return {"status": "escalated"}

    attempts += 1
    deps.emit("fix", {"attempt": attempts, "max_attempts": MAX_FIX_ATTEMPTS,
                      "action": action, "reason_fa": reason})
    return _decide(
        state,
        {"plan": plan, "fix_attempts": attempts, "status": "running",
         "fix_history": list(state.get("fix_history", [])) + [{"attempt": attempts, "action": action}]},
        f"fix#{attempts}: {action}",
    )


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
    return {"status": "running" if res.get("ok") else "deploy_failed"}


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
    rep = {
        "status": "needs_user_decision",
        "reason": "حلقه‌ی خودکار به سقف ۳ تلاش رسید؛ تصمیم با تو است.",
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
