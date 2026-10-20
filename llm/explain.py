"""explain(risk, level, concepts) -> Persian teaching/terse text.

Costs come ONLY from /data/prices.yaml; every value there is UNVERIFIED until
filled from Liara's pricing page, so cost lines carry the UNVERIFIED badge.
"""

from __future__ import annotations

import functools
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRICES_PATH = ROOT / "data" / "prices.yaml"

FIX_ACTIONS: dict[str, str] = {
    "R1_sqlite_without_disk": "قبل از استقرار یک دیسک ماندگار بساز و مسیر دیتابیس را در liara.json به آن mount کن.",
    "R2_default_secret": "رمز پیش‌فرض را عوض کن؛ مقدار جدید فقط در متغیر محیطیِ مقصد بگذارد و مقدار قبلی rotate شود.",
    "R3_vendor_sdk_dependency": "رفتار این SDK روی لیارا را زودتر تست کن؛ اگر به سرویس سازنده وابسته است برایش پشتیبان بگذار.",
    "R4_lockfile_or_package_manager_mismatch": "یک lockfile انتخاب کن و بقیه را حذف کن؛ با npm استقرار می‌دهی، package-lock.json بساز.",
    "R5_junk_included": "قبل از zip کردن، node_modules و .next و فایل‌های دیتابیس محلی را از بسته حذف کن.",
    "R6_missing_env_vars": "نام متغیرها را به .env.example اضافه کن و مقدارشان را در محیط مقصد ست کن.",
}


@functools.lru_cache(maxsize=1)
def prices(path: str | None = None) -> dict:
    p = pathlib.Path(path) if path else PRICES_PATH
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def estimate_cost() -> dict:
    """The ONLY cost source. Anything UNVERIFIED stays unknown — never invented."""
    data = prices()
    val = data.get("plans", {}).get("nano", {}).get("monthly_usd", "UNVERIFIED")
    unverified = not isinstance(val, (int, float))
    return {
        "estimated_cost": "نامشخص (UNVERIFIED)" if unverified else val,
        "cost_unverified": unverified,
    }


def _cost_line() -> str:
    c = estimate_cost()
    return f"هزینه: {c['estimated_cost']}"


def fake_explain(risk: dict, level: str, concepts: dict) -> str:
    """Deterministic offline explanation — visibly different per level."""
    rid = risk.get("id", "")
    action = FIX_ACTIONS.get(rid, "ابتدا لاگ‌ها را بررسی کن؛ علت قطعی از inspection معلوم نیست.")
    if level == "expert":
        return f"{rid}: {risk.get('detail', '')} اقدام: {action} {_cost_line()}."

    parts = [f"🔍 ریسک «{rid.replace('_', ' ')}»:", risk.get("detail", "")]
    parts.append("چرا برای تو مهم است؟ چند مفهوم پشت این ریسک است:")
    for key in risk.get("concept_keys", []):
        c = concepts.get(key)
        if c:
            parts.append(f"• {key}: {c['beginner_fa']}")
    parts.append(f"✅ اقدام بعدی: {action}")
    parts.append(f"💰 {_cost_line()} — قبل از هر اقدام هزینه‌دار، از تو تأیید می‌گیرم.")
    return "\n".join(p for p in parts if p)


def real_explain(client, risk: dict, level: str, concepts: dict) -> str:
    from llm.prompts import EXPLAIN_SYSTEM, build_explain_user

    return client.complete_text(EXPLAIN_SYSTEM, build_explain_user(risk, level, concepts))


def explain(client, risk: dict, level: str, concepts: dict) -> str:
    """Dispatch: FakeLLM has a deterministic path; real clients get the prompt."""
    if getattr(client, "name", "") == "fake":
        return fake_explain(risk, level, concepts)
    return real_explain(client, risk, level, concepts)
