"""Care Agent — نگهبان پروژه (post-deploy guardian, T-care).

چیزی که داورهای چالش خواستند: بعد از استقرار، ایجنت باید *بماند* و مثل یک
مشاور بهینه‌سازی عمل کند — دوره‌ای سایت را چک کند، لاگ‌ها را بخواند، وقتی
سرعت یا سلامت افت کرد بگوید *چرا* و *چطور* درست شود، و خلاصه‌ای آماده بدهد
که کاربر بتواند به هر ایجنت دیگری بدهد.

طراحی:
- run_care_check(session, trigger): یک چرخهٔ کامل پایش (read-only + ۱ فراخوان LLM)
  ۱) پروب واقعی HTTP چندمرحله‌ای روی URL زنده (status + زمان پاسخ هر نمونه)
  ۲) لاگ زندهٔ لیارا (GET /v1/projects/{name}/logs) — خطوط پرریسک جدا می‌شوند
  ۳) متریک summary (اگر deployer از API پشتیبانی کند — همیشه UNVERIFIED-labelled)
  ۴) تحلیل LLM -> نمرهٔ سلامت، حکم فارسی، یافته‌ها با شدت، توصیه‌های عملی
  ۵) ذخیره در session.care_reports (RAM) + SSE event «care_report»
- دوره‌ها: daily / weekly / monthly. فعال‌سازی فوراً یک پایش اجرا می‌کند
  (کاربر در همان ثانیه‌ها نتیجه را می‌بیند — حیاتی برای دمو) و بعد طبق دوره.
- CareScheduler: یک ترد daemon؛ هر ۳۰ ثانیه بیدار می‌شود و پایش‌های سررسید
  را اجرا می‌کند. هرگز چیزی نمی‌نویسد/حذف نمی‌کند — فقط می‌خواند و پیشنهاد می‌دهد.
- build_agent_brief(session): یک بریف فارسی (LLM) که کاربر کپی می‌کند و به
  ایجنت خودش می‌دهد: «تو نگهبان این سایت هستی…».

LlmNote: پایش مصرف LLM دارد؛ اما هر چرخه فقط ONE call است و RunBudget جلسه +
LLM_MAX_CALLS + fallback قانونی (بدون LLM) کفِ هزینه را می‌بندد.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

# ---------------------------------------------------------------- schedule

SCHEDULES: dict[str, float] = {
    "daily": 86400.0,        # هر روز
    "weekly": 604800.0,      # هر هفته
    "monthly": 2592000.0,    # هر ماه (۳۰ روز)
}
SCHEDULE_FA = {
    "daily": "هر روز",
    "weekly": "هر هفته",
    "monthly": "هر ماه",
}
SCHEDULE_LABELS = (
    {"value": "daily", "label": "هر روز"},
    {"value": "weekly", "label": "هر هفته"},
    {"value": "monthly", "label": "هر ماه"},
)

CARE_HISTORY_MAX = 20
MAX_LOG_LINES_FOR_LLM = 40
MAX_PROBES = 3

SCHEDULER_TICK_S = 30.0


# ---------------------------------------------------------------- LLM schemas

class CareFinding(BaseModel):
    severity: str = Field(default="info")     # critical | warning | info
    title_fa: str = ""
    detail_fa: str = ""
    evidence: str = ""


class CareAnalysis(BaseModel):
    score: int = Field(default=80)
    verdict_fa: str = ""
    findings: list[CareFinding] = Field(default_factory=list)
    tips_fa: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- probes

def probe_url(url: str | None, attempts: int = MAX_PROBES,
              timeout: float = 8.0) -> dict:
    """پروب واقعی چندمرحله‌ای: چند GET پشت‌سرهم؛ status + زمان هر نمونه.
    میانگین/بدترین زمان پاسخ مبنای قضاوت سرعت است (evidence، نه تخمین)."""
    out: dict[str, Any] = {"url": url, "ok": False, "samples": [],
                           "status_code": 0, "error": ""}
    if not url:
        out["error"] = "no_url"
        return out
    for _ in range(max(1, attempts)):
        start = time.monotonic()
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as c:
                r = c.get(url)
            ms = int((time.monotonic() - start) * 1000)
            out["samples"].append({"status": r.status_code, "ms": ms})
            out["status_code"] = r.status_code
            if r.status_code < 500:
                out["ok"] = True
        except httpx.HTTPError as e:
            out["samples"].append({"status": 0, "ms": int((time.monotonic() - start) * 1000)})
            out["error"] = type(e).__name__
        time.sleep(0.3)
    return out


def probe_summary(probe: dict) -> dict:
    """خلاصهٔ پروب برای نمایش/پرامپت: میانگین و بدترین زمان پاسخ موفق."""
    samples = [s for s in probe.get("samples", []) if s.get("ms") is not None]
    ok_samples = [s for s in samples if s.get("status") and s["status"] < 500]
    if not ok_samples:
        return {"ok": bool(probe.get("ok")), "avg_ms": None, "worst_ms": None,
                "status_code": probe.get("status_code", 0), "n": len(samples)}
    mss = [s["ms"] for s in ok_samples]
    return {"ok": bool(probe.get("ok")), "avg_ms": int(sum(mss) / len(mss)),
            "worst_ms": max(mss), "status_code": probe.get("status_code", 0),
            "n": len(samples)}


RISKY_LOG_RE = None  # compiled lazily


def risky_log_lines(lines: list[str], limit: int = 8) -> list[str]:
    """خطوط پرریسک لاگ — شواهد خام برای LLM و برای حالت بدون LLM."""
    import re

    global RISKY_LOG_RE
    if RISKY_LOG_RE is None:
        RISKY_LOG_RE = re.compile(
            r"(error|fatal|exception|traceback|refused|denied|timeout|timed out|"
            r"out of memory|oom|killed|crash|exit(ed)?( code)?|fail|warn|429|5\d\d)",
            re.IGNORECASE)
    picked = [ln.strip()[:220] for ln in lines if RISKY_LOG_RE.search(ln)]
    return picked[-limit:]


# ---------------------------------------------------------------- LLM analysis

def _evidence_blob(session) -> dict:
    """جمع‌آوری شواهد (read-only) — همان چیزی که به LLM می‌رود (redacted by client)."""
    deployer = session.deployer
    report = session.report or {}
    url = report.get("url")
    probe = probe_url(url)
    logs: list[str] = []
    try:
        logs = deployer.logs() if deployer is not None else []
    except Exception:  # noqa: BLE001 — لاگ حق حیات را از پایش نمی‌گیرد
        logs = []
    metrics = None
    try:
        metrics = deployer.metrics_summary() if deployer is not None else None
    except Exception:  # noqa: BLE001
        metrics = None
    plan_id = None
    try:
        plan_id = deployer.current_plan() if deployer is not None else None
    except Exception:  # noqa: BLE001
        plan_id = None
    return {"url": url, "probe": probe, "summary": probe_summary(probe),
            "logs": logs[-60:], "risky": risky_log_lines(logs),
            "metrics": metrics, "plan_id": plan_id}


CARE_SYSTEM = (
    "تو «نگهبان پروژه» هستی: یک مشاور بهینه‌سازی وب برای برنامه‌نویس تازه‌کار فارسی‌زبان. "
    "با شواهد داده‌شده (پروب HTTP، لاگ، متریک) قضاوت کن. هرگز چیزی از خودت نمی‌سازی؛ "
    "اگر شواهد سالم‌اند، بگو سالم‌اند. یافته‌ها را با شدت (critical|warning|info) بده و "
    "برای هر مشکل راه‌حل عملی و مشخص بگو (نه کلی). خروجی فقط JSON."
)


def _fallback_analysis(ev: dict) -> dict:
    """تحلیل قانونی بدون LLM — همیشه در دسترس."""
    s = ev.get("summary") or {}
    findings: list[dict] = []
    tips: list[str] = []
    score = 90
    if not s.get("ok"):
        score = 30
        findings.append({"severity": "critical", "title_fa": "آدرس زنده پاسخ نمی‌دهد",
                         "detail_fa": "پروب HTTP نتوانست پاسخ سالم (<500) بگیرد. اگر برنامه تازه build شده، چند دقیقه صبر کن؛ وگرنه لاگ‌های runtime را در پنل لیارا ببین.",
                         "evidence": f"status={s.get('status_code', 0)} err={ev.get('probe', {}).get('error', '')}"})
    else:
        avg = s.get("avg_ms")
        if avg is not None and avg > 1500:
            score = 65
            findings.append({"severity": "warning", "title_fa": "زمان پاسخ کند است",
                             "detail_fa": f"میانگین زمان پاسخ {avg} میلی‌ثانیه است. برای شروع: کش کردن پاسخ‌های ثابت، کم‌کردن کار سنگین در مسیر درخواست اصلی، و در صورت نیاز ارتقای پلن.",
                             "evidence": f"avg_ms={avg}"})
        elif avg is not None:
            tips.append(f"زمان پاسخ خوب است ({avg}ms) — همین‌طور نگه‌ش دار.")
    risky = ev.get("risky") or []
    if risky:
        score = min(score, 75)
        findings.append({"severity": "warning", "title_fa": "خطاها/هشدارهایی در لاگ دیده می‌شود",
                         "detail_fa": "در لاگ برنامه الگوهای خطا/هشدار پیدا شد. اگر تعدادشان رو به رشد است، منشأشان را برطرف کن؛ نمونه‌ها در شواهد آمده‌اند.",
                         "evidence": risky[0][:160]})
    if not findings:
        tips.append("الان هیچ نشانهٔ مشکل فعال پیدا نکردم؛ طبق زمان‌بندی دوباره چک می‌کنم.")
    return {"score": score, "verdict_fa": ("وضعیت سایت پایدار به نظر می‌رسد." if s.get("ok") else "سایت در وضعیت ناسالم است؛ اقدام لازم است."),
            "findings": findings[:6], "tips_fa": tips[:4]}


CARE_JSON_SPEC = (
    'فقط JSON با دقیقاً این شکل برگردان: '
    '{"score": عدد 0 تا 100, "verdict_fa": "یک جملهٔ فارسی روشن", '
    '"findings": [{"severity": "critical|warning|info", "title_fa": "عنوان کوتاه", '
    '"detail_fa": "توضیح فارسی و راه‌حل مشخص", "evidence": "شاهد خام از شواهد"}], '
    '"tips_fa": ["توصیهٔ عملی فارسی"]} — هیچ متن بیرون از JSON ننویس.'
)


def _llm_analysis(session, ev: dict) -> dict:
    """یک call؛ خطا/بودجه/دمو -> fallback قانونی (هرگز پایش را نمی‌شکند)."""
    ensure = _ensure_llm(session)
    if not ensure:
        return _fallback_analysis(ev)
    import json as _json

    user = {
        "app_url": ev.get("url"),
        "plan_id": ev.get("plan_id"),
        "probe": ev.get("summary"),
        "risky_log_lines": ev.get("risky"),
        "metrics_unverified": ev.get("metrics"),
        "recent_log_tail": (ev.get("logs") or [])[-MAX_LOG_LINES_FOR_LLM:],
    }
    try:
        out = session.llm.complete_json(
            CARE_SYSTEM,
            "شواهد:\n" + _json.dumps(user, ensure_ascii=False)[:6000] +
            "\n\nمقیاس نمره: 0 تا 100 (زیر 50 یعنی بحرانی). حکم یک جملهٔ فارسی روشن است. "
            "تا ۶ یافته و تا ۴ توصیهٔ عملی فارسی بده.\n" + CARE_JSON_SPEC,
            CareAnalysis, lambda: _fallback_analysis(ev))
        score = int(out.get("score", 80))
        return {
            "score": max(0, min(100, score)),
            "verdict_fa": str(out.get("verdict_fa", ""))[:300],
            "findings": [
                {"severity": (f.get("severity") if f.get("severity") in
                              ("critical", "warning", "info") else "info"),
                 "title_fa": str(f.get("title_fa", ""))[:160],
                 "detail_fa": str(f.get("detail_fa", ""))[:600],
                 "evidence": str(f.get("evidence", ""))[:200]}
                for f in (out.get("findings") or [])[:6]
            ],
            "tips_fa": [str(t)[:240] for t in (out.get("tips_fa") or [])[:4]],
        }
    except Exception:  # noqa: BLE001 — LLM خواب بود، قانونی حرف می‌زنیم
        return _fallback_analysis(ev)


def _ensure_llm(session) -> bool:
    """LLM جلسه را مطمئن شو (بدون ساخت دوباره). True = واقعی/قابل استفاده."""
    try:
        from app.runner import ensure_llm

        ensure_llm(session)
        return session.llm is not None
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- check cycle

def run_care_check(session, trigger: str = "manual") -> dict | None:
    """یک چرخهٔ کامل پایش. هم‌زمان‌سازی با care_lock؛ None = skip (بدون URL یا در حال اجرا)."""
    if session.care_running:
        return None
    url = (session.report or {}).get("url")
    if not url:
        return None
    with session.care_lock:
        if session.care_running:
            return None
        session.care_running = True
    try:
        ev = _evidence_blob(session)
        analysis = _llm_analysis(session, ev)
        rec = {
            "ts": round(time.time(), 3),
            "trigger": trigger,          # scheduled | manual | first
            "url": url,
            "health": ev.get("summary"),
            "probe_samples": (ev.get("probe") or {}).get("samples", []),
            "risky_log_lines": ev.get("risky"),
            "metrics": ev.get("metrics"),
            "plan_id": ev.get("plan_id"),
            "score": analysis["score"],
            "verdict_fa": analysis["verdict_fa"],
            "findings": analysis["findings"],
            "tips_fa": analysis["tips_fa"],
        }
        with session._cond:
            session.care_reports = (session.care_reports + [rec])[-CARE_HISTORY_MAX:]
        session.add_event("care_report", {"trigger": trigger, "score": rec["score"],
                                          "verdict_fa": rec["verdict_fa"],
                                          "ts": rec["ts"]})
        return rec
    finally:
        session.care_running = False


def set_schedule(session, schedule: str) -> dict:
    """فعال/غیرفعال‌سازی پایش دوره‌ای. فعال‌سازی -> فوراً یک پایش در ترد پس‌زمینه."""
    if schedule not in ("off", "daily", "weekly", "monthly"):
        return {"ok": False, "message_fa": "زمان‌بندی نامعتبر است."}
    if schedule == "off":
        session.care_schedule = ""
        session.care_next_ts = 0.0
        session.add_event("care_schedule", {"schedule": "off"})
        return {"ok": True, "schedule": ""}
    session.care_schedule = schedule
    # اولین پایش فوری (در ترد جدا تا endpoint سریع برگردد)
    session.care_next_ts = time.time() + SCHEDULES[schedule]
    session.add_event("care_schedule", {"schedule": schedule,
                                        "next_ts": session.care_next_ts,
                                        "first_check": True})
    threading.Thread(target=_run_first_check, args=(session,), daemon=True,
                     name=f"care-first-{session.id}").start()
    return {"ok": True, "schedule": schedule, "next_ts": session.care_next_ts}


def _run_first_check(session) -> None:
    try:
        run_care_check(session, trigger="first")
    except Exception:  # noqa: BLE001 — اولین پایش هرگز نمی‌تواند بالا بیاورد
        pass


def reschedule(session) -> None:
    """بعد از هر پایش زمان‌بندی‌شده، سررسید بعدی را بگذار."""
    if session.care_schedule in SCHEDULES:
        session.care_next_ts = time.time() + SCHEDULES[session.care_schedule]


# ---------------------------------------------------------------- agent brief

BRIEF_SYSTEM = (
    "یک «بریف نگهداری» فارسی برای ایجنت دیگری می‌نویسی. کاربر این متن را کپی می‌کند و "
    "به ایجنت خودش (مثلاً یک چت‌بات یا ابزار کدنویسی) می‌دهد. متن باید: نقش ایجنت را "
    "تعریف کند (نگهبان سایت)، آدرس سایت و پلن و متغیرهای کلیدی را بیاورد، بگوید چه "
    "چیزهایی را هر بار چک کند (سلامت HTTP، زمان پاسخ، لاگ‌ها، مصرف منابع)، آستانه‌های "
    "هشدار، و اینکه فقط گزارش بدهد و هیچ اقدامی بدون اجازهٔ صاحب سایت انجام ندهد. "
    "متن نهایی خودشِ بریف است — بدون مقدمه و بدون توضیح اضافه."
)


def build_agent_brief(session) -> str:
    """بریف آماده برای ایجنت کاربر (LLM؛ خطا -> نسخهٔ قانونی). Cache در session."""
    if session.care_brief:
        return session.care_brief
    plan = session.plan or {}
    report = session.report or {}
    facts = {
        "url": report.get("url"),
        "platform": plan.get("platform"),
        "plan_id": (session.care_reports[-1].get("plan_id") if session.care_reports
                    else None),
        "env_keys": [e.get("key") for e in (plan.get("env_needed") or [])
                     if isinstance(e, dict)],
        "schedule": SCHEDULE_FA.get(session.care_schedule, "در صورت نیاز"),
        "last_score": (session.care_reports[-1].get("score") if session.care_reports
                       else None),
    }
    if not _ensure_llm(session):
        session.care_brief = _fallback_brief(facts)
        return session.care_brief
    import json as _json

    try:
        text = session.llm.complete_text(
            BRIEF_SYSTEM,
            "مشخصات پروژهٔ استقرارشده:\n" + _json.dumps(facts, ensure_ascii=False) +
            "\n\nبریف مستقیم با شماره‌گذاری ۱) ۲) ۳)… شروع شود؛ بدون مقدمه.")
        # Guard: a stub/deterministic client may return a placeholder (e.g.
        # "fake") — that is not a brief. The honest fallback is better.
        if text and len(text.strip()) >= 120:
            session.care_brief = text.strip()[:4000]
        else:
            session.care_brief = _fallback_brief(facts)
    except Exception:  # noqa: BLE001
        session.care_brief = _fallback_brief(facts)
    return session.care_brief


def _fallback_brief(f: dict) -> str:
    return (
        "تو نگهبان سایت من هستی و هر بار این کارها را انجام می‌دهی:\n"
        f"۱) به آدرس {f.get('url') or '(آدرس سایت)'} یک درخواست HTTP بزن و وضعیت پاسخ و زمان آن را ثبت کن. "
        "اگر کد وضعیت ۵xx بود یا زمان پاسخ از ۲ ثانیه گذشت، هشدار بده.\n"
        "۲) لاگ‌های تازهٔ برنامه را بخوان و دنبال error، traceback، out of memory و تکرار خطاها بگرد؛ "
        "الگوهای پرتکرار را با نمونهٔ واقعی گزارش کن.\n"
        "۳) مصرف منابع (CPU/حافظه/دیسک) را چک کن و روند رشد غیرعادی را اعلام کن.\n"
        f"۴) طبق زمان‌بندی «{f.get('schedule', 'در صورت نیاز')}» خلاصه‌ای فارسی از وضعیت بده: "
        "نمرهٔ سلامت ۰ تا ۱۰۰، مشکلات به ترتیب اهمیت، و پیشنهاد مشخص رفع.\n"
        "قواعد: هیچ اقدامی (ری‌استارت، تغییر پلن، حذف) بدون اجازهٔ صریح من انجام نده؛ "
        "پیشنهاد بده، تأیید از من باشد. اطلاعات محرمانه (توکن‌ها) را هیچ‌جا تکرار نکن."
    )


# ---------------------------------------------------------------- scheduler

class CareScheduler(threading.Thread):
    """ترد سراسری: هر TICK ثانیه، جلسه‌های دارای schedule و URL زنده را چک می‌کند."""

    def __init__(self, store) -> None:
        super().__init__(daemon=True, name="care-scheduler")
        self.store = store
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.wait(SCHEDULER_TICK_S):
            try:
                self._tick()
            except Exception:  # noqa: BLE001 — scheduler هرگز نمی‌میرد
                pass

    def _tick(self) -> None:
        now = time.time()
        with self.store._glock:
            sessions = list(self.store._sessions.values())
        for s in sessions:
            if not s.care_schedule or s.care_schedule not in SCHEDULES:
                continue
            if s.care_next_ts and now >= s.care_next_ts:
                s.care_next_ts = now + SCHEDULES[s.care_schedule]  # جلوگیری از دوباره‌کاری
                threading.Thread(target=self._run_scheduled, args=(s,), daemon=True,
                                 name=f"care-run-{s.id}").start()

    @staticmethod
    def _run_scheduled(session) -> None:
        try:
            rec = run_care_check(session, trigger="scheduled")
            if rec is not None:
                reschedule(session)
        except Exception:  # noqa: BLE001
            pass


_SCHEDULER: CareScheduler | None = None


def start_scheduler(store) -> CareScheduler:
    """یک scheduler سراسری در startup (هرگز دوبار شروع نمی‌شود)."""
    global _SCHEDULER
    if _SCHEDULER is not None and _SCHEDULER.is_alive():
        return _SCHEDULER
    _SCHEDULER = CareScheduler(store)
    _SCHEDULER.start()
    return _SCHEDULER
