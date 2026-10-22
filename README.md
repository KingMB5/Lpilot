# LiaraPilot

**An agentic deployment copilot for [Liara](https://liara.ir) (Iranian PaaS) — built for the
Starcoach Agentic AI Hackathon (Liara challenge).**

You finished your project but dread deploying it. LiaraPilot takes your project zip, gauges your
experience with 4 questions, inspects the project deterministically, explains the risks in plain
Persian (teaching concepts in beginner mode), asks for your explicit approval — especially for
cost — deploys to Liara, reads the logs, **self-fixes failures (max 3 attempts)**, verifies the
live URL with a real HTTP check, monitors the app after release (health + CPU/RAM metrics), and
hands you a final report. Mid-run, you can ask it free questions — one LLM call per question.

Two modes driven by one `level` value:

- **beginner** — teaches each concept with a simple analogy while working; ends with a
  "what you learned" checklist.
- **expert** — terse; only errors, costs, and decisions.

## Repository layout

This repo is the **Python product only**. Cloud-sandbox tooling (a Next.js shell, Caddy config,
supervisor scripts) exists on disk in some environments but is deliberately untracked — see the
"Cloud-sandbox tooling" section at the bottom of `.gitignore`.

```
agent/      LangGraph graph + nodes (assess → inspect → explain → plan → [approval] → deploy → fix ≤3 → verify → report)
app/        FastAPI: JSON/SSE API (contract v1), zip-safety, sessions, runner, redaction
deployers/  Deployer interface: MockDeployer | LiaraApiDeployer (official HTTP API) | LiaraCliDeployer
llm/        OpenAI-compatible client + FakeLLM fallback, diagnose (allowlisted actions), mid-run Q&A
inspector/  Deterministic rules R1–R6 — no AI in inspection, ever
web/        Single-page Persian RTL UI (plain HTML/CSS/JS, no build step)
config/     defaults.yaml (VERIFIED facts w/ sources + UNVERIFIED list), spec/paas.yaml (official OpenAPI snapshot)
data/       concepts.json (15-concept glossary), assessment.json, prices.yaml (all UNVERIFIED)
fixtures/   Demo projects incl. the Persian perfume store (Next+Prisma+SQLite) and broken-project
docs/       DEPLOY_AGENT.md, DEMO_SCRIPT.md, VERIFIED_MATRIX.md, ARCHITECTURE.md
tests/      pytest suite (respx-mocked HTTP; no network, no secrets)
scripts/    demo_mock.py (offline E2E script), live_check.py (staged real-API probe, dry-run default)
```

## Run — DEMO mode (fully offline, safe)

One-click launchers: `run_demo.bat` (Windows) / `run_demo.sh` (Linux & macOS).

Or manually — Linux / macOS:

```bash
pip install -r requirements.txt
DEMO_MODE=1 uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Windows (PowerShell):

```powershell
pip install -r requirements.txt
$env:DEMO_MODE="1"
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Windows (cmd):

```bat
set DEMO_MODE=1
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Then open http://localhost:3000 — demo badge on, FakeLLM + MockDeployer, zero external calls.

Demo fixtures: `next-prisma-sqlite` (the perfume store — yields risks R1–R4), `node-express`,
`plain-html`, `broken-project` (drives the failure → diagnose → ask-user → self-fix loop).

## Run — REAL mode (real LLM, real Liara API)

```bash
cp .env.example .env   # then fill at least LLM_API_KEY (and LIARA_PLAN_ID / token per session)
DEMO_MODE=0 DEPLOYER=liara_api uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Windows PowerShell equivalent: `$env:DEMO_MODE="0"; $env:DEPLOYER="liara_api"; python -m uvicorn app.main:app --host 0.0.0.0 --port 3000`

- LLM: any OpenAI-compatible endpoint via `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`
  (BoxAPI defaults are pre-filled in `.env.example`; they are not secrets). `LLM_MAX_CALLS` caps
  billable calls per run; FakeLLM is only ever a fallback (demo mode / missing key / schema
  failure after retry).
- Liara: the agent talks to the official PaaS HTTP API (Bearer auth). Every request shape is
  verified against the official OpenAPI spec snapshot `config/spec/paas.yaml` and recorded in
  `config/defaults.yaml`; anything the spec does not define stays UNVERIFIED and is labelled in
  the UI.
- First real deploy: keep `LIARA_DRY_RUN=1` — the deployer builds and validates every payload,
  prints it redacted, and sends nothing.

Staged real-API probe (auth → list apps → create test app → disks/env → upload → logs/status →
restart-with-approval), defaulting to dry-run: `python scripts/live_check.py --help`.

## Deploying the agent itself on Liara

Two paths, both documented in `docs/DEPLOY_AGENT.md`:

- **Plan A — `python` platform**: `liara.json` at repo root already declares
  `{"platform": "python", "port": 3000}` (VERIFIED platform value) + pinned `requirements.txt`.
  The buildpack's build/run detection is UNVERIFIED until the first real deploy.
- **Plan B — `docker` platform (deterministic fallback)**: the root `Dockerfile` builds the agent
  on `python:3.12-slim`, exposes 3000, and runs uvicorn — same code path as local runs.
  `docker` is a VERIFIED liara.json platform value.

The agent's own healthcheck is `GET /api/health` (booleans only — no secret values, ever).

**Public deployment protection (required):** set `RATE_LIMIT_PER_MIN` (per-IP sliding
window) and `RATE_LIMIT_GLOBAL_M` (process-wide ceiling) in the app's env on Liara —
e.g. `60` / `600`. Both are off by default for local use; `/api/health` is always exempt.
`LLM_MAX_CALLS` caps billable LLM calls per run regardless of traffic.

### Verify against the real API before a real deploy

```bash
LIARA_API_TOKEN=<token-from-panel> python3 scripts/live_check.py            # tier-1 real reads + tier-2 dry-run
LIARA_PLAN_ID=ir-small LIARA_API_TOKEN=<token> python3 scripts/live_check.py --live   # smallest plan; creates a probe app — delete it from the panel afterwards
```

Every step prints PASS/FAIL/UNVERIFIED with the raw (redacted) error and appends a dated row
to `docs/VERIFIED_MATRIX.md`.

## Tests

```bash
python -m pytest tests/ -q
```

Covers: inspector rules, experience scoring, graph transitions, approval gate, fix-loop cap and
allowlist, HTTP contract via respx (mocked, no network), risk-tier gating, metrics fetch,
zip-slip/size/count caps, redaction of every secret in every event/prompt/log, and the "no
uploaded code is ever executed" guarantee.

## Security

- The user's Liara token lives in RAM for one session only; it is registered in a secret
  redactor so it can never appear in SSE events, LLM prompts, logs, or the report.
- `set_env` with a secret-looking key is rejected unless a human supplies the value via an
  ask-user interrupt.
- Uploaded code is inspected, never executed. Zip extraction is slip/size/count hardened.
- Paid/destructive operations are gated: see the three-tier risk matrix in
  `docs/ARCHITECTURE.md` (automatic / approval-required / never-offered).
- Full threat model and secret-handling rules: [`SECURITY.md`](SECURITY.md).
- Audits (2026-10-22): `gitleaks` — no leaks in working tree **or** full git history (40 commits);
  `bandit` — no issues; `pip-audit` on pinned `requirements.txt` — no known vulnerabilities.

## خلاصه‌ی فارسی

**لیاراپایلوت** دستیارِ استقرارِ ایجنتیک برای پلتفرم لیاراست: zip پروژه را می‌گیرد، با ۴ سؤال سطح
تجربه‌ات را می‌سنجد، پروژه را به‌صورت قطعی بازرسی می‌کند (بدون هوش مصنوعی)، ریسک‌ها را فارسی و
سطح‌به‌سطح توضیح می‌دهد، پیش از هر کار هزینه‌دار اجازه‌ی صریح می‌گیرد، روی لیارا مستقر می‌کند،
لاگ‌ها را می‌خواند و تا ۳ بار خودش مشکل را اصلاح می‌کند (اگر مقدار رمزی لازم باشد از خودت
می‌پرسد، هرگز خودش نمی‌سازد)، آدرس زنده را با HTTP واقعی چک می‌کند و بعد از انتشار هم وضعیت و
مصرف منابع (CPU/RAM از API متریک لیارا) را پایش می‌کند. وسط کار هم می‌توانی سؤال بپرسی.
حالت «مبتدی» مفاهیم را با تشبیه یاد می‌دهد و در پایان چک‌لیست «چه یاد گرفتی» می‌دهد؛ حالت
«خبره» فقط خطا، هزینه و تصمیم‌ها را می‌گوید. هیچ قیمتی از خودش ساخته نمی‌شود (UNVERIFIED تا پر
شدن از منابع رسمی)، هیچ رمزی لاگ یا ذخیره نمی‌شود، و هر کاری که سطح ریسک ۲ داشته باشد (ری‌استارت،
تغییر پلن، تغییر env/دستور اجرا، دیپلوی جدید) فقط با تأیید صریح تو انجام می‌شود.
