"""Agentic fix diagnosis (T14) — the LLM proposes, deterministic code validates and applies.

Contract: the LLM receives a REDACTED log tail (chars capped), the inspection
summary and the deploy config (env KEYS only — never values) and must return
{diagnosis_fa, action, params, confidence}.

`action` is allowlisted: set_env, change_port, change_start_command, add_disk,
change_platform, ask_user, give_up. Anything else is rejected — first by the
pydantic validator (which triggers the client's one-retry path), then by
validate_params, and the deterministic fallback diagnosis is applied instead.

Security rules enforced here (AGENTS.md):
- set_env with a secret-looking key and a NON-empty value is rejected: secret
  values must come from a HUMAN via ask_user, never from the model.
- The fallback diagnosis is fully deterministic (derived from the failure kind,
  the project files and the logs) — it is also what FakeLLM returns offline.
- Max attempts is enforced in agent/nodes.py, never delegated to the model.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import load_defaults

LOG_TAIL_CHARS = 2000  # redacted log tail handed to the model

ALLOWED_ACTIONS = ("set_env", "change_port", "change_start_command", "add_disk",
                   "change_platform", "ask_user", "give_up")

ALLOWLIST_SHAPES = {
    "set_env": {"key": "str", "value": "str (empty ok; secret-looking keys FORBIDDEN -> ask_user)"},
    "change_port": {"port": "int 1..65535"},
    "change_start_command": {"start_command": "str"},
    "add_disk": {"name": "str", "size": "str optional", "mount_to": "str"},
    "change_platform": {"platform": "one of the verified liara.json platform values"},
    "ask_user": {"question_fa": "str"},
    "give_up": {},
}


class CauseModel(BaseModel):
    """One ranked possible cause for an UNKNOWN failure (T3) — evidence must
    quote the (already redacted) log, the model must not invent new facts."""

    cause_fa: str = Field(min_length=3)
    evidence: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class DiagnosisModel(BaseModel):
    """Schema for the fix-loop decision. An invalid `action` raises here, which
    the LLM client treats as a validation failure (one retry, then fallback).
    `causes_ranked` carries the ranked-cause analysis for failures that are NOT
    one of the scripted scenarios (T3 unknown-error path); the action stays
    allowlisted or the run escalates (give_up) with the explanation."""

    diagnosis_fa: str = Field(min_length=3)
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    causes_ranked: list[CauseModel] = Field(default_factory=list, max_length=3)

    @field_validator("action")
    @classmethod
    def _allowlisted(cls, v: str) -> str:
        if v not in ALLOWED_ACTIONS:
            raise ValueError(f"action '{v}' is not in the allowlist {list(ALLOWED_ACTIONS)}")
        return v


DIAGNOSE_SYSTEM = (
    "تو «خلبان استقرار» هستی؛ استقرار روی لیارا شکست خورده و باید از روی لاگ‌ها علت را تشخیص بدهی.\n"
    "قوانین سفت‌وسخت:\n"
    "1) فقط و فقط JSON برگردان: {\"diagnosis_fa\", \"action\", \"params\", \"confidence\", "
    "\"causes_ranked\"}.\n"
    "2) action فقط یکی از لیست مجاز باشد و params دقیقاً شکل اعلام‌شده را داشته باشد.\n"
    "3) فقط بر اساس لاگ و inspection داده‌شده تصمیم بگیر؛ هیچ رفتار پلتفرمی یا قیمتی از خودت نساز.\n"
    "4) اگر مقدار یک متغیر رمزی لازم است، هرگز خودت مقدار نساز؛ action=ask_user بده تا کاربر بدهد.\n"
    "5) اگر مشکل بیرون از اختیار توست، action=give_up بده (این یعنی ارجاع به کاربر با توضیح).\n"
    "6) اگر در متن ***REDACTED*** دیدی همان‌طور نگهش دار و تلاش برای بازیابی نکن.\n"
    "7) اگر شکست از الگوهای شناخته‌شده نیست، در causes_ranked تا حداکثر ۳ علت محتمل را به\n"
    "   ترتیب احتمال با شاهدی از خود لاگ بیاور؛ هرگز شاهد از خودت نساز؛ و action را فقط از\n"
    "   لیست مجاز انتخاب کن یا give_up بده."
)


def build_diagnose_context(state: dict, kind: str, log_tail: str) -> dict:
    """Everything the model may see. Env VALUES never enter this payload — keys only."""
    inspection = state.get("inspection") or {}
    plan = state.get("plan") or {}
    approvals = state.get("approvals") or {}
    env_keys = sorted({str(k) for k in (list(plan.get("env_placeholders") or {})
                                        + list((approvals.get("env_values") or {}))
                                        + list(plan.get("env_fixes") or {}))})
    return {
        "level": state.get("level", "beginner"),
        "failure": {"kind": kind, "message": (state.get("deploy_result") or {}).get("message", "")},
        "log_tail": log_tail,
        "inspection": {
            "platform": inspection.get("platform"),
            "port": inspection.get("port"),
            "start_command": inspection.get("start_command"),
            "env_vars_required": list(inspection.get("env_vars_required") or []),
            "risk_ids": [r.get("id") for r in inspection.get("risks", [])],
        },
        "deploy_config": {
            "app_name": plan.get("app_name"),
            "platform": plan.get("platform") or inspection.get("platform"),
            "port": plan.get("port"),
            "start_command": plan.get("start_command") or inspection.get("start_command"),
            "env_keys": env_keys,
            "disks": plan.get("disks") or [],
        },
        "fix_attempts": int(state.get("fix_attempts", 0)),
        "max_attempts": 3,
        "allowlist": ALLOWLIST_SHAPES,
    }


def build_diagnose_user(ctx: dict) -> str:
    return json.dumps({"task": "diagnose_deploy_failure", "output_language": "fa", **ctx},
                      ensure_ascii=False)


# ------------------------------------------------------------- validation

def _secret_key(key: str) -> bool:
    pats = load_defaults().get("security", {}).get("secret_key_patterns", [])
    k = key.lower()
    return any(p in k for p in pats)


def validate_params(action: str, params: dict, state: dict) -> tuple[bool, str]:
    """Deterministic param checks — the model's proposal is a suggestion, this is the law."""
    if action not in ALLOWED_ACTIONS:
        return False, f"action '{action}' not allowlisted"
    if action == "set_env":
        key = str(params.get("key") or "").strip()
        if not key:
            return False, "set_env needs a non-empty key"
        if "value" not in params or not isinstance(params.get("value"), str):
            return False, "set_env needs a string value"
        if params.get("value", "").strip() and _secret_key(key):
            return False, "set_env with a secret-looking key must use ask_user (human provides the value)"
        return True, ""
    if action == "change_port":
        try:
            port = int(params.get("port"))
        except (TypeError, ValueError):
            return False, "change_port needs an integer port"
        if not (1 <= port <= 65535):
            return False, f"port {port} out of range"
        return True, ""
    if action == "change_start_command":
        cmd = str(params.get("start_command") or "").strip()
        if not cmd or len(cmd) > 300:
            return False, "change_start_command needs a short non-empty command"
        return True, ""
    if action == "add_disk":
        if not str(params.get("name") or "").strip():
            return False, "add_disk needs a name"
        if not str(params.get("mount_to") or "").strip():
            return False, "add_disk needs mount_to"
        return True, ""
    if action == "change_platform":
        allowed = set(load_defaults().get("deploy", {}).get("platform_values", []))
        p = str(params.get("platform") or "").strip()
        if p not in allowed:
            return False, f"platform '{p}' not in verified liara.json values {sorted(allowed)}"
        return True, ""
    if action == "ask_user":
        if not str(params.get("question_fa") or "").strip():
            return False, "ask_user needs question_fa"
        return True, ""
    if action == "give_up":
        return True, ""
    return False, "unreachable"  # pragma: no cover


# ------------------------------------------------------------- application

def apply_action(action: str, params: dict, state: dict) -> tuple[dict, str]:
    """Deterministically apply a validated action to a deep copy of the plan.
    Returns (plan, human description fa)."""
    plan = copy.deepcopy(state.get("plan") or {})
    desc = ""
    if action == "set_env":
        key = str(params["key"]).strip()
        plan.setdefault("env_fixes", {})[key] = str(params.get("value", ""))
        desc = f"متغیر محیطی «{key}» در کانفیگ استقرار تنظیم شد."
    elif action == "change_port":
        plan["port"] = int(params["port"])
        desc = f"پورت به {plan['port']} تغییر کرد."
    elif action == "change_start_command":
        plan["start_command"] = str(params["start_command"]).strip()
        desc = f"دستور اجرا به «{plan['start_command']}» تغییر کرد."
    elif action == "add_disk":
        disk = {"name": str(params["name"]).strip(), "mountTo": str(params["mount_to"]).strip()}
        if str(params.get("size") or "").strip():
            disk["size"] = str(params["size"]).strip()
        disks = [d for d in (plan.get("disks") or []) if d.get("name") != disk["name"]]
        disks.append(disk)
        plan["disks"] = disks
        desc = f"دیسک ماندگار «{disk['name']}» به برنامه اضافه شد (mount: {disk['mountTo']})."
    elif action == "change_platform":
        plan["platform"] = str(params["platform"]).strip()
        desc = f"پلتفرم به «{plan['platform']}» تغییر کرد."
    return plan, desc


def safe_params(action: str, params: dict) -> dict:
    """Event-safe copy of params — set_env values are ALWAYS masked."""
    p = dict(params or {})
    if action == "set_env" and "value" in p:
        p["value"] = "***"
    return p


# ------------------------------------------------------------- fallback brain

_ENTRY_RE = re.compile(r"^(?:node|bun|deno|python3?)\s+\.?/?(\S+)$")


def _start_command_of(state: dict) -> str:
    plan = state.get("plan") or {}
    inspection = state.get("inspection") or {}
    return str(plan.get("start_command") or inspection.get("start_command") or "").strip()


def _start_candidate(state: dict) -> str | None:
    """Read the project (never executes it): find a root entry file that exists
    and is not the one that just failed. Returns 'node <file>' or None."""
    project_dir = state.get("project_dir")
    start = _start_command_of(state)
    if not project_dir or not start:
        return None
    m = _ENTRY_RE.match(start)
    if not m:
        return None
    missing = m.group(1)
    ext = pathlib.Path(missing).suffix or ".js"
    root = pathlib.Path(project_dir)
    if not root.is_dir():
        return None
    preferred = ("server", "app", "index", "main")
    candidates = []
    for p in sorted(root.glob(f"*{ext}")):
        rel = p.relative_to(root).as_posix()
        if rel == missing or p.name.startswith((".", "_")):
            continue
        candidates.append(rel)
    candidates.sort(key=lambda c: (next((i for i, w in enumerate(preferred)
                                         if pathlib.Path(c).stem == w), len(preferred)), c))
    runner = "python3" if ext == ".py" else "node"
    return f"{runner} {candidates[0]}" if candidates else None


def _missing_env_keys(state: dict) -> list[str]:
    plan = state.get("plan") or {}
    approvals = state.get("approvals") or {}
    have = dict(approvals.get("env_values") or {})
    have.update(plan.get("env_fixes") or {})  # fixes from earlier attempts count as filled
    return [k for k in plan.get("env_needed", [])
            if not str(have.get(k, "")).strip()]


def deterministic_diagnosis(kind: str, state: dict) -> dict:
    """The scripted diagnosis — FakeLLM's brain offline AND the validator's fallback.
    Deterministic by design: same state -> same diagnosis (AGENTS.md)."""
    if kind == "bad_port":
        return {"diagnosis_fa": "پورت اعلام‌شده در liara.json نامعتبر است؛ به پیش‌فرض مستند ۳۰۰۰ برمی‌گردیم.",
                "action": "change_port", "params": {"port": 3000}, "confidence": 0.95}
    if kind == "missing_env_var":
        missing = _missing_env_keys(state)
        if missing:
            return {"diagnosis_fa": "متغیرهای محیطی لازم مقدار ندارند؛ این چیزی است که فقط تو می‌توانی بدهی.",
                    "action": "ask_user",
                    "params": {"question_fa": "مقدار این متغیرهای محیطی را بفرست (با یک پیام): " + "، ".join(missing)},
                    "confidence": 0.9}
        return {"diagnosis_fa": "متغیرهای لازم مقدار دارند ولی سرویس قبول نکرد؛ بررسی دستی لازم است.",
                "action": "give_up", "params": {}, "confidence": 0.4}
    if kind == "wrong_start_script":
        candidate = _start_candidate(state)
        if candidate:
            return {"diagnosis_fa": f"فایل شروع «{_start_command_of(state)}» پیدا نشد؛ اما «{candidate}» در پروژه هست.",
                    "action": "change_start_command", "params": {"start_command": candidate},
                    "confidence": 0.85}
        return {"diagnosis_fa": "اسکریپت شروع به فایلی اشاره می‌کند که وجود ندارد و جایگزین معتبری هم در پروژه نیست.",
                "action": "ask_user",
                "params": {"question_fa": f"دستور شروع «{_start_command_of(state)}» کار نمی‌کند؛ دستور درست را بفرست."},
                "confidence": 0.8}
    if kind == "missing_disk_sqlite":
        size = os.getenv("LIARA_DISK_SIZE", "1G")
        return {"diagnosis_fa": "دیتابیس SQLite به دیسک ماندگار نیاز دارد (الزام مستند لیارا)؛ دیسک می‌سازم و mount می‌کنم.",
                "action": "add_disk",
                "params": {"name": "database", "size": size, "mount_to": "db"},
                "confidence": 0.9}
    return {"diagnosis_fa": "از لاگ‌ها علت قابل‌استخراجی نیافتم؛ ادامه‌ی خودکار مسئولانه نیست.",
            "action": "give_up", "params": {}, "confidence": 0.3}
