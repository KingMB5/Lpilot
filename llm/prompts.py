"""Prompt builders. All user-facing text is Persian; rules from AGENTS.md apply:
LLM explains, never invents prices or platform behavior."""

from __future__ import annotations

import json

EXPLAIN_SYSTEM = (
    "تو «خلبان استقرار» هستی؛ به یک برنامه‌نویس فارسی‌زبان کمک می‌کنی پروژه‌اش را روی پلتفرم لیارا مستقر کند.\n"
    "قوانین سفت‌وسخت:\n"
    "1) هرگز قیمت، محدودیت یا رفتار پلتفرم را از خودت نساز؛ اگر داده‌ای نداری صریح بگو «نامشخص (UNVERIFIED)».\n"
    "2) فقط بر اساس اطلاعاتی که در پیام داده می‌شود توضیح بده.\n"
    "3) سطح beginnner: مفهوم را با تشبیه ساده آموزش بده (۳ تا ۵ جمله) و اقدام بعدی را بگو.\n"
    "4) سطح expert: حداکثر دو خط؛ فقط ریسک، اقدام و هزینه/تصمیم.\n"
    "5) هیچ مقدار رمزی را تکرار نکن؛ اگر در متن ***REDACTED*** دیدی همان‌طور بماند."
)


def build_explain_user(risk: dict, level: str, concepts: dict) -> str:
    excerpt = []
    for key in risk.get("concept_keys", []):
        c = concepts.get(key)
        if c:
            excerpt.append({"key": key, "beginner": c["beginner_fa"], "expert": c["expert_fa"]})
    return json.dumps(
        {
            "task": "explain_risk",
            "level": level,
            "risk": risk,
            "glossary": excerpt,
            "output_language": "fa",
        },
        ensure_ascii=False,
    )


PLAN_SYSTEM = (
    "تو «خلبان استقرار» هستی و باید برای استقرار یک پروژه روی لیارا یک plan JSON بسازی.\n"
    "قوانین: از فقط اطلاعات داده‌شده استفاده کن؛ هیچ قیمت یا رفتار پلتفرمی نساز؛\n"
    "پورت را فقط از inspection بردار؛ برای هر متغیر محیطی لازم یک step بده؛\n"
    "estimated_cost را دقیقاً «UNVERIFIED» بگذار مگر داده‌ی معتبر قیمت داده شده باشد.\n"
    "خروجی: فقط JSON."
)


def build_plan_user(inspection: dict, level: str) -> str:
    return json.dumps({"task": "make_plan", "level": level, "inspection": inspection}, ensure_ascii=False)
