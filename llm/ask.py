"""Mid-run Q&A (T15) — the user asks a free question while the agent works.

Rules (AGENTS.md + task spec):
- Max ONE LLM call per question (enforced by construction in app/main.py; the
  ask_lock serializes concurrent asks).
- The answer is grounded ONLY in the glossary + current session state + level.
  No invention of prices/limits/API behavior: anything unknown stays UNVERIFIED.
- Env VALUES never enter the context (keys only) and the answer is redacted
  through the session SecretRegister before it leaves the server.
- FakeLLM answers deterministically from the glossary — demo mode never calls out.
"""

from __future__ import annotations

import json

from app.redact import SecretRegister, redact_text

ASK_SYSTEM = (
    "تو «خلبان استقرار» هستی؛ کاربر وسط کار یک سؤال آزاد می‌پرسد.\n"
    "قوانین سفت‌وسخت:\n"
    "1) فقط بر اساس واژه‌نامه و وضعیت جلسه که در پیام داده می‌شود جواب بده؛ از دانش بیرونی برای رفتار لیارا استفاده نکن.\n"
    "2) هیچ قیمت، محدودیت یا رفتار پلتفرمی نساز؛ اگر داده‌ای نداری صریح بگو «نامشخص (UNVERIFIED)».\n"
    "3) هیچ مقدار رمزی را تکرار نکن؛ اگر ***REDACTED*** دیدی همان‌طور نگهش دار. مقادیر متغیرهای محیطی را هرگز نگو.\n"
    "4) سطح beginner: ۳ تا ۵ جمله با تشبیه ساده؛ سطح expert: حداکثر دو خط.\n"
    "5) پاسخ فارسی و کوتاه باشد."
)

# Persian/English keywords -> glossary concept key (deterministic matcher)
_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("پورت", "port"), "port"),
    (("متغیر محیطی", "محیطی", "env"), "environment_variable"),
    (("بیلد", "build", "کامپایل"), "build"),
    (("اجرا", "استارت", "start"), "start_command"),
    (("دیسک", "disk"), "persistent_disk"),
    (("لاگ", "log"), "logs"),
    (("دامنه", "دامین", "domain", "آدرس"), "domain"),
    (("هزینه", "قیمت", "پلن", "plan", "cost"), "plan_resources"),
    (("پلتفرم", "platform"), "platform"),
    (("لاک", "lockfile", "lock"), "lockfile"),
    (("رمز", "راز", "secret", "پسورد", "گذرواژه"), "secret"),
    (("دیتابیس", "database", "sqlite", "prisma", "پریزما"), "database"),
    (("منطقه", "region", "ایران", "آلمان"), "region"),
    (("بازگردانی", "rollback", "ریلیز", "release"), "rollback"),
    (("سلامت", "health"), "health_check"),
]

COST_LINE = "قیمت‌ها هنوز تأیید نشده‌اند (UNVERIFIED) و من قیمت نمی‌سازم؛ پیش از هر کار هزینه‌دار از تو تأیید می‌گیرم."

GENERIC_FA = (
    "سؤال تو را دقیق از واژه‌نامه‌ام نگرفتم. می‌توانی درباره‌ی پورت، متغیر محیطی، بیلد، "
    "دیسک، لاگ، دامنه، پلن یا دیتابیس بپرسی؛ یا صبر کن خلبان گزارش مرحله‌ی بعد را بدهد."
)


def match_concepts(question: str, limit: int = 3) -> list[str]:
    q = question.lower()
    hits: list[str] = []
    for keys, concept in _KEYWORDS:
        if any(k in q for k in keys) and concept not in hits:
            hits.append(concept)
        if len(hits) >= limit:
            break
    return hits


def glossary_excerpt(concepts: dict, keys: list[str]) -> list[dict]:
    out = []
    for key in keys:
        c = concepts.get(key)
        if c:
            out.append({"key": key, "beginner": c["beginner_fa"], "expert": c["expert_fa"]})
    return out


def build_ask_user(level: str, question: str, excerpt: list[dict], summary: dict) -> str:
    return json.dumps({
        "task": "answer_question",
        "output_language": "fa",
        "level": level,
        "question": question,
        "glossary": excerpt,
        "session_state": summary,
    }, ensure_ascii=False)


def fake_answer(question: str, level: str, concepts: dict, summary: dict) -> str:
    """Deterministic offline answer — glossary keyword match + one state line."""
    keys = match_concepts(question)
    state_line = _state_line(summary)
    if not keys:
        return GENERIC_FA + ("\n" + state_line if state_line else "")
    parts = []
    if any(k in ("plan_resources",) for k in keys):
        parts.append(COST_LINE)
    for key in keys:
        c = concepts.get(key)
        if not c:
            continue
        parts.append(c["expert_fa"] if level == "expert" else c["beginner_fa"])
    if state_line:
        parts.append(state_line)
    return "\n".join(parts)


def _state_line(summary: dict) -> str:
    if not summary or summary.get("status") in ("created", None):
        return ""
    stage = summary.get("stage_fa") or ""
    status = summary.get("status") or ""
    line = f"وضعیت فعلی: {stage or status}"
    url = summary.get("url")
    if url:
        line += f" — آدرس زنده: {url}"
    return line


def answer_question(client, register: SecretRegister, level: str, question: str,
                    summary: dict, concepts: dict) -> str:
    """One LLM call (real clients) or the deterministic glossary answer (FakeLLM).
    The answer is redacted before returning — defense in depth."""
    level = level if level in ("beginner", "expert") else "beginner"
    if getattr(client, "name", "") == "fake":
        return redact_text(fake_answer(question, level, concepts, summary), register)
    excerpt = glossary_excerpt(concepts, match_concepts(question))
    try:
        text = client.complete_text(ASK_SYSTEM, build_ask_user(level, question, excerpt, summary))
    except Exception:  # budget/client failure -> deterministic answer, no second call
        text = fake_answer(question, level, concepts, summary) + "\n(پاسخ قطعی — دسترسی مدل نبود)"
    return redact_text(text, register)
