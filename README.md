# LiaraPilot

**An agentic deployment copilot for [Liara](https://liara.ir) (Iranian PaaS) — built for the
Starcoach Agentic AI Hackathon (Liara challenge).**

You finished your project but dread deploying it. LiaraPilot takes your project zip, gauges your
experience with 4 questions, inspects the project deterministically, explains the risks in plain
Persian (teaching concepts in beginner mode), asks for your explicit approval — especially for
cost — deploys to Liara, reads the logs, **self-fixes failures (max 3 attempts)**, verifies the
live URL with a real HTTP check, and hands you a final report. Mid-run, you can ask it free
questions and it answers from its glossary + your session state — one LLM call per question.

Two modes driven by one `level` value:

- **beginner** — teaches each concept with a simple analogy while working; ends with a
  "what you learned" checklist.
- **expert** — terse; only errors, costs, and decisions.

## Architecture

```
zip upload ──> FastAPI (app/)  ──> LangGraph graph (agent/)
   │              │                    │  assess → inspect → explain → plan
   │              │                    │  → [approval interrupt] → prepare → deploy
   │              │                    │  → read_logs → fix(≤3) → verify → report
   │              │                    └─ teach_summary (beginner only)
   │              ├── deployers/  MockDeployer | LiaraApiDeployer (HTTP, paas.yaml) | LiaraCliDeployer (stub)
   │              ├── llm/         OpenAI-compatible client | FakeLLM | diagnose | ask
   │              ├── inspector/   deterministic rules R1–R6 (no AI)
   │              └── web/        single-page Persian RTL UI (plain HTML/CSS/JS, no build step)
   └── events     SSE timeline with Last-Event-ID replay, approval/question modals
```

Design rules (AGENTS.md, binding): **deterministic code executes and enforces limits; the LLM
only explains and proposes.** Every LLM output is schema-validated (one retry, then a
deterministic fallback). Every prompt, event and log line passes a secret redactor. Prices are
never invented — they come only from `data/prices.yaml` and are UNVERIFIED until filled from
official sources. Facts proven against live docs live in `config/defaults.yaml` with source URL
+ date; everything else is marked UNVERIFIED.

## Quickstart (fully offline demo)

```bash
pip install -r requirements.txt
DEMO_MODE=1 uvicorn app.main:app --host 0.0.0.0 --port 3000
# open http://localhost:3000 — demo badge on, FakeLLM + MockDeployer, zero external calls
```

Fixtures for the demo flow: `next-prisma-sqlite` (the Persian perfume store — yields risks
R1–R4), `node-express`, `plain-html`, `broken-project` (drives the failure → self-fix loop).

## Tests

```bash
python -m pytest tests/ -q     # 98 tests: inspector rules, scoring, graph transitions,
                               # approval gate, fix cap, HTTP contract (respx), security
```

Security highlights covered by tests: zip-slip/size/count caps, uploaded code never executed,
secrets redacted in every SSE event, LLM prompt, deployer log and report; fix-loop actions
allowlisted and validated (`set_env` with a secret value is rejected — a human must supply it);
the user's Liara token lives in RAM for one session and is never logged.

## Going live (Thursday runbook)

See `docs/DEPLOY_AGENT.md`. Short version: `LIARA_DRY_RUN=1` prints and validates every request
payload (secrets redacted) without sending; then the real run uses the official PaaS API
(Bearer auth; endpoints/payloads verified against `developers.liara.ir/spec/paas.yaml` and
recorded in `config/defaults.yaml`). The agentic fix loop diagnoses from the redacted log tail
and proposes allowlisted actions only.

Docs: `docs/DEMO_SCRIPT.md` — the 3-minute demo, scene by scene (Persian).

## خلاصه‌ی فارسی

**لیاراپایلوت** دستیارِ استقرارِ ایجنتیک برای پلتفرم لیاراست: zip پروژه را می‌گیرد، با ۴ سؤال سطح
تجربه‌ات را می‌سنجد، پروژه را به‌صورت قطعی بازرسی می‌کند (بدون هوش مصنوعی)، ریسک‌ها را فارسی و
سطح‌به‌سطح توضیح می‌دهد، پیش از هر کار هزینه‌دار اجازه‌ی صریح می‌گیرد، روی لیارا مستقر می‌کند،
لاگ‌ها را می‌خواند و تا ۳ بار خودش مشکل را اصلاح می‌کند (اگر مقدار رمزی لازم باشد از خودت
می‌پرسد، هرگز خودش نمی‌سازد)، آدرس زنده را با HTTP واقعی چک می‌کند و گزارش می‌دهد. وسط کار هم
می‌توانی سؤال بپرسی. حالت «مبتدی» مفاهیم را با تشبیه یاد می‌دهد و در پایان چک‌لیست «چه یاد گرفتی»
می‌دهد؛ حالت «خبره» فقط خطا، هزینه و تصمیم‌ها را می‌گوید. هیچ قیمتی از خودش ساخته نمی‌شود
(UNVERIFIED تا پر شدن از منابع رسمی) و هیچ رمزی لاگ یا ذخیره نمی‌شود.
</content>
