# LiaraPilot — Architecture

> Persian summary at the bottom. This document covers the four required deliverable points:
> architecture, Liara API connection, solution limitations, and user interaction.

## 1. Architecture (short)

```
zip upload ──> FastAPI (app/)  ──> LangGraph graph (agent/)
   │              │                    │  assess → inspect → explain → plan
   │              │                    │  → [approval interrupt] → prepare → deploy
   │              │                    │  → read_logs → fix(≤3) → verify → report
   │              │                    └─ teach_summary (beginner only)
   │              ├── deployers/  MockDeployer | LiaraApiDeployer (HTTP) | LiaraCliDeployer (stub)
   │              ├── llm/         OpenAI-compatible client (+FakeLLM fallback) | diagnose | ask
   │              ├── inspector/   deterministic rules R1–R6 — inspection is NEVER AI
   │              ├── agent/risk.py  three-tier permission gate (see §4)
   │              └── web/        single-page Persian RTL UI (plain HTML/CSS/JS)
   └── events     SSE timeline (Last-Event-ID replay), approval/question modals
```

**Core rule:** deterministic code executes, validates and enforces every limit; the LLM only
explains and proposes. Every LLM output is schema-validated (pydantic, one retry, then a
deterministic fallback). Every prompt, event and log line passes a secret redactor. Uploaded
code is inspected, never executed.

## 2. How it connects to the Liara API

- **Client**: pure-Python `LiaraApiDeployer` (httpx), `Authorization: Bearer <token>`.
  Base `https://api.liara.ir`. The user's token lives in RAM for one session (or `LIARA_API_TOKEN`
  for scripting) and is registered in a redactor so it can never be logged.
- **Contract**: every request/response shape is verified against the official OpenAPI spec,
  snapshotted at `config/spec/paas.yaml` (checked 2026-10-20) and recorded as facts in
  `config/defaults.yaml` (`paas_api_endpoints`, `metrics_endpoints`, `restart_endpoint`, …).
- **Deploy sequence**: GET app (404→create with console-provided `planID`) → ensure disks →
  update-envs → multipart source upload → poll status → verify the live URL with a real HTTP GET.
- **Read-only ops**: logs (`since` REQUIRED), status, metrics summary — feeding the fix loop,
  the monitor and the UI.
- **Retry policy**: 429/5xx/timeout get exponential backoff (our policy — the spec declares no
  429/5xx behavior); 401/404 fail fast.
- **dry-run**: `LIARA_DRY_RUN=1` builds + validates every payload, prints it redacted, sends nothing.
- **Live probe**: `scripts/live_check.py` runs staged real-API checks (auth → list → create test
  app → disks/env → upload → logs/status → restart-with-approval), defaulting to dry-run, and
  updates `docs/VERIFIED_MATRIX.md`.

## 3. Known limitations (announced in the UI, per the challenge rules)

- **No plan/price catalog endpoint exists** in the API (grep evidence in
  `docs/METRICS_VERIFICATION.md`) → prices/planIDs cannot be read live. `data/prices.yaml` stays
  an all-UNVERIFIED fallback; the cost approval always shows «UNVERIFIED».
- **`planID` values are not in the spec** — provided via `LIARA_PLAN_ID` from the console.
- **Metrics units/scaling are UNVERIFIED** until the first live read; values are shown with an
  UNVERIFIED badge and the inference fallback is labelled «استنتاج».
- **Log `since` value format is UNVERIFIED** (the parameter itself is required by the spec) —
  configurable via `LIARA_LOGS_SINCE`.
- **Disk-size units UNVERIFIED**; **live-URL format** (`https://{app}.liara.run`) UNVERIFIED —
  the verify step does a real HTTP check instead of trusting the format.
- Mounting happens via `liara.json` disks at deploy time (no attach-disk endpoint exists).
- Domain purchase/Irnic setup is guided in text only (beginner mode) — not automated.

## 4. Risk matrix — who may do what (T4)

| Tier | Label (UI) | Actions | Enforcement |
|------|-----------|---------|-------------|
| **1 — automatic** | «خودکار — فقط خواندنی» | read status, read logs, read metrics/monitor sample | read-only code paths; no endpoint that mutates is reachable from them |
| **2 — approval required** | «نیازمند تأیید تو» | deploy / new version, restart, plan resize, set_env, change_port, change_start_command, add_disk, change_platform | initial deploy & fix-loop changes: LangGraph approval interrupt (cost + env values shown); post-deploy ops: `POST /api/sessions/{id}/ops` refuses with `403 approval_required` unless `approved:true` for that exact action (denials emit a visible `ops_denied` event) |
| **3 — forbidden / manual-only** | «فقط دستی — ایجنت هرگز اجرا نمی‌کند» | delete app (`DELETE /v1/projects/{name}`), delete disk | never offered in UI, never in the LLM allowlist, `agent.risk.gate()` always refuses — even with approval |

Test pinning the gate: `tests/test_risk.py` — a tier-2 action without approval is never executed;
tier-3 is refused unconditionally; the LLM allowlist contains no tier-3 action.

## 5. User interaction (UX)

- One RTL Persian page, two modes from a single `level`: **beginner** (concepts taught with
  analogies, ends with a "what you learned" checklist) and **expert** (only errors, costs,
  decisions).
- Everything the agent does appears on one timeline (SSE): stages, risks, plan, deploy logs,
  fixes (with risk-tier badges), verification, report.
- Approval modal: estimated cost with UNVERIFIED badge, editable env table, prepared files —
  nothing paid happens without an explicit click.
- Mid-run free Q&A («سؤال از خلبان») — one LLM call per question, grounded in the glossary +
  current state, redacted.
- Post-deploy: monitor panel (health + CPU/RAM with source labels), restart button behind the
  tier-2 approval modal.

---

## خلاصه‌ی فارسی

معماری: FastAPI + LangGraph + رابط استقرار قابل‌تعویض (Mock/LiaraApi/CLI). قاعده‌ی مرکزی: «کد
قطعی اجرا می‌کند و محدود می‌سازد؛ مدل فقط توضیح می‌دهد و پیشنهاد می‌دهد». اتصال به لیارا از راه
API رسمی HTTP با Bearer و شکل‌های تأییدشده در برابر OpenAPI رسمی (snapshot در `config/spec/`)
انجام می‌شود؛ اول dry-run، بعد اجرای واقعی. محدودیت‌ها (نداشتن endpoint قیمت/پلن، واحدهای
متریک، فرمت since و …) صریحاً UNVERIFIED اعلام می‌شوند. ماتریس ریسک سه‌سطحی: خواندنی‌ها خودکار؛
ری‌استارت/تغییر پلن/env/دیسک/دیپلوی فقط با تأیید صریح؛ حذف اپ/دیسک هرگز توسط ایجنت انجام
نمی‌شود. UX: یک صفحه‌ی فارسی RTL، تایم‌لاین زنده، مودال تأیید با هزینه و envها، پرسش آزاد وسط کار،
و پایش بعد از انتشار.
