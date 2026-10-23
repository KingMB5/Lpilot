"""Deterministic plan builder + pydantic schema. The LLM may shape wording;
the structure and limits here are enforced in code (AGENTS.md)."""

from __future__ import annotations

import json
from typing import List

from pydantic import BaseModel, Field

from llm.explain import estimate_cost


class PlanFile(BaseModel):
    name: str
    content: str


class PlanStep(BaseModel):
    title_fa: str
    detail: str = ""


class PlanModel(BaseModel):
    steps: List[PlanStep] = Field(default_factory=list)
    port: int | None = None
    platform: str = "unknown"
    env_needed: List[str] = Field(default_factory=list)
    prepared_files: List[PlanFile] = Field(default_factory=list)
    estimated_cost: str = "نامشخص (UNVERIFIED)"
    cost_unverified: bool = True


def build_liara_json(inspection: dict, port: int | None) -> str:
    """VERIFIED schema (config/defaults.yaml): platform values + disks[].
    Python: the start command MUST travel as liara.json "args" — Liara's
    python buildpack ignores Procfile and defaults to `python3 main.py`
    (docs.liara.ir/paas/python/how-tos/deploy-app, checked live 2026-10-24)."""
    spec: dict = {"platform": inspection.get("platform", "node")}
    if port:
        spec["port"] = port
    if (str(spec["platform"]).lower() == "python"
            and inspection.get("start_command")):
        spec["args"] = [str(inspection["start_command"])]
    risk_ids = {r.get("id") for r in inspection.get("risks", [])}
    if "R1_sqlite_without_disk" in risk_ids:
        spec["disks"] = [{"name": "database", "mountTo": "db"}]
    return json.dumps(spec, indent=2, ensure_ascii=False)


def liara_json_from_plan(plan: dict) -> str:
    """Regenerate liara.json from the (possibly self-fixed) plan — same VERIFIED
    schema; the plan is the single source of truth after fix actions."""
    spec: dict = {"platform": plan.get("platform") or "node"}
    if plan.get("port"):
        spec["port"] = plan["port"]
    if (str(spec["platform"]).lower() == "python"
            and plan.get("start_command")):
        spec["args"] = [str(plan["start_command"])]
    if plan.get("disks"):
        spec["disks"] = [{"name": d["name"], "mountTo": d["mountTo"]}
                         for d in plan["disks"] if d.get("name") and d.get("mountTo")]
    return json.dumps(spec, indent=2, ensure_ascii=False)


def build_plan_deterministic(inspection: dict, level: str) -> dict:
    """The fallback/authoritative plan — no LLM required, no invention allowed."""
    port = inspection.get("port")
    env_needed = list(inspection.get("env_vars_required") or [])
    cost = estimate_cost()
    steps: list[dict] = []

    platform = inspection.get("platform", "unknown")
    steps.append({"title_fa": f"شناسایی پروژه: پلتفرم {platform}",
                  "detail": f"پورت تشخیص‌داده‌شده: {port if port else 'نامشخص — پیش‌فرض ۳۰۰۰ بسته می‌شود'}"})
    if inspection.get("build_command"):
        steps.append({"title_fa": "بیلد گرفتن روی سرور",
                      "detail": f"دستور: {inspection['build_command']}"})
    if env_needed:
        steps.append({"title_fa": "تنظیم متغیرهای محیطی",
                      "detail": "مقادیر را خودت در گام تأیید وارد می‌کنی: " + "، ".join(env_needed)})
    steps.append({"title_fa": "استقرار روی لیارا", "detail": "با Deployer انتخاب‌شده (mock یا واقعی)"})
    steps.append({"title_fa": "بررسی سلامت آدرس زنده", "detail": "درخواست HTTP به آدرس نهایی"})

    files = [{"name": "liara.json", "content": build_liara_json(inspection, port or 3000)}]
    risk_ids = {r.get("id") for r in inspection.get("risks", [])}
    import uuid

    return {
        # unique per run — concurrent users never collide on one app name
        "app_name": "lp-" + uuid.uuid4().hex[:6],
        "steps": steps,
        "port": port or 3000,
        "platform": platform,
        "start_command": inspection.get("start_command"),
        "env_needed": env_needed,
        "prepared_files": files,
        "disks": [{"name": "database", "mountTo": "db"}] if "R1_sqlite_without_disk" in risk_ids else [],
        "estimated_cost": cost["estimated_cost"],
        "cost_unverified": cost["cost_unverified"],
    }
