# Worklog — LiaraPilot

---
Task ID: 0
Agent: Z.ai Code (lead build engineer)
Task: Save hackathon operating rules into AGENTS.md at repo root; produce critical
pre-build analysis (overlooked items, risks, questions, folder structure, 3-day plan).
No product code yet.

Work Log:
- Inspected repo root (/home/z/my-project): Next.js sandbox scaffolding present,
  no AGENTS.md, no worklog.md.
- Wrote /home/z/my-project/AGENTS.md with the full rule set (role, brief assumption,
  product concept, operator profile, decision authority, architecture, workflow,
  debug protocol) — rules saved verbatim in English.
- Best-effort doc verification against docs.liara.ir (fetched live, not from memory):
  - VERIFIED `liara deploy` flags: `--api-token`, `--path`, `--platform`, `-a/--app`,
    `-p/--port`, `--detach`, `--no-app-logs`, `--build-arg`, `--no-cache`,
    `-b iran|germany`, `--team-id`, `--liara-json`
    (https://docs.liara.ir/references/cli/deploy-app/)
  - VERIFIED SQLite on Node.js apps REQUIRES creating a disk (e.g. `database`) and
    mounting it, configured via liara.json; official example uses raw `sqlite3` module
    (https://docs.liara.ir/paas/nodejs/how-tos/connect-to-db/sqlite/)
  - VERIFIED official Liara Prisma guide uses PostgreSQL only
    (https://docs.liara.ir/paas/nodejs/how-tos/connect-to-db/prisma/)
  - VERIFIED no official Next.js guide in Liara Node platform docs (Nuxt, Remix,
    Nitro, NestJS... exist) (sidebar of /paas/nodejs/getting-started/)
  - VERIFIED docs exist for: env vars, hooks, disk usage, Node version selection.
  - Pricing: NOT collected — remains UNVERIFIED. Never invent.
- Drafted critical analysis for Mehran (in Persian): 7 overlooked items, risk table,
  5 questions, proposed structure (FastAPI backend + plain RTL frontend), 3-day plan.

Stage Summary:
- AGENTS.md is the binding rulebook at repo root; worklog.md is the shared log.
- Biggest product risk identified: primary test project (Next.js + Prisma + SQLite)
  sits exactly on Liara's least-documented path (no Next.js guide, Prisma guide is
  Postgres-only, SQLite needs a disk). Day-1 spike mandatory; keep a fallback sample.
- CLI automation looks feasible (token + detach flags verified) but exit-code/log
  parsing strategy must be proven in the Day-1 spike.
- Next step (after Mehran's answers): Wed-night skeleton — FastAPI + StaticFiles
  frontend + FakeLLM + MockDeployer E2E, facts recorded in config with sources.

---
Task ID: 1 (audit + T1 amendment + T8 dispatch)
Agent: Z.ai Code (lead build engineer)
Task: Audit pre-existing T1 commit, verify Mehran's Liara claims, finalize API contract, dispatch T8 UI subagent.

Work Log:
- Audited repo: commit e957870 (T1 scaffold) existed from prior session; .venv deps already installed and pinned versions verified importable (fastapi 0.150.0, langgraph 1.2.16, openai 3.35.1).
- Found half-finished T2 files (app/config.py OK, inspector/core.py missing rules.py) — T2 to be completed by me.
- Verified on developers.liara.ir + docs.liara.ir (2026-10-20): OpenAPI HTTP API exists (POST /v2/projects/{name}/sources, GET /v1/projects/{name}/logs, POST /v1/projects/update-envs, disks endpoints, wss shell); liara.json platform values include "next" (Mehran was right); disks schema {"disks":[{"name","mountTo"}]}.
- Recorded 4 new VERIFIED facts + 1 recommendation in config/defaults.yaml (commit c43ffb9).
- LangGraph 1.2.16 interrupt round-trip probed successfully (invoke -> __interrupt__, Command(resume=...) resumes).

Stage Summary:
- API CONTRACT v1 (binding for app + web):
  POST /api/upload (multipart "file") -> {session_id}
  POST /api/sessions/demo {fixture} -> {session_id}
  GET  /api/assessment -> {questions:[{id,text,options:[{value,label}]}], expert_skip:{value,label}}
  POST /api/sessions/{id}/answers {answers, skip_expert} -> {level}
  POST /api/sessions/{id}/start -> 202
  GET  /api/sessions/{id} -> snapshot {id,demo,level,status,stage_fa,events:[{i,type,payload,ts}],plan,report,approval}
  GET  /api/events/{id} (SSE, Last-Event-ID replay, heartbeat 15s)
  POST /api/sessions/{id}/approve {approved,cost_ok,env_values} -> 202
  POST /api/sessions/{id}/level {level} ; POST /api/sessions/{id}/cancel -> 202
  SSE types: stage, log, risk, plan, approval_required, deploy_log, fix, verify, report, done, error, cancelled, level_changed
- Deployer decision (Q5): pure-Python LiaraApiDeployer via HTTP API preferred; CLI stays fallback. Exact deploy payload schema still UNVERIFIED (check paas.yaml Thursday).
---
Task ID: T8
Agent: frontend-styling-expert (subagent)
Task: Build Persian RTL dark-premium single-page web UI (web/index.html, app.js, style.css)

Work Log:
- Read worklog.md (API CONTRACT v1) + AGENTS.md; created /home/z/my-project/web/ with exactly 3 files: index.html (6269 B), app.js (44632 B), style.css (24170 B). No other project file touched; no build step, no CDN, no webfonts (system stack: Vazirmatn, IRANSans, Segoe UI, Tahoma).
- index.html: dir="rtl" lang="fa" single page with 5 static sections (upload / assessment / run / report / expired) toggled by JS, sticky top bar (LiaraPilot brand + hidden #demo-badge), aria-live timeline <ol>, modal overlay shell, inline SVG icons only, noscript fallback.
- style.css: dark-premium tokens (#0d0f13 bg, #151922 surfaces, amber #f59e0b primary, emerald #10b981 success, red #ef4444, zinc low); zero blue/indigo hexes (verified by grep); .ltr-block exactly per spec + overflow-wrap; rounded-xl cards, soft shadows, amber focus-visible rings, reduced-motion support, 360px breakpoints.
- app.js (one IIFE, `store` state object, English comments): fetch wrapper `req()` centralizes JSON errors and routes 404 {detail:session_expired}-like bodies to the expired screen; screens A–D wired to CONTRACT v1 endpoints (upload multipart "file", demo {fixture}, assessment, answers{answers,skip_expert}, start, snapshot, approve{approved,cost_ok,env_values}, level, cancel).
- SSE: EventSource on /api/events/{id}, manual reconnect (close -> 2s -> same URL, no cache-busters, max 10 attempts -> expired screen); snapshot hydration renders events oldest-first and dedupes replay via max-i id gate; all 13 SSE types routed (stage/log/risk/plan/approval_required/deploy_log/fix/verify/report/done/error/cancelled/level_changed); deploy_log grouped under «استقرار»; plan card de-duplicated by replacement.
- Approval modal (real dialog, focus trap, labelled env inputs): cost + UNVERIFIED badge, editable env table, collapsible prepared files, «تأیید و استقرار» (cost_ok:true implied by click) / «لغو»; 202 closes modal and appends timeline note; pending approval also restored from snapshot.approval or newest approval_required event after refresh.
- Level toggle: segmented «توضیح بیشتر/خلاصه‌تر», optimistic update + risk-body re-render from in-memory risk list, server echo/level_changed resync, revert on failure; cancel button always visible while running.
- Report screen: status header mapping (موفق / نیاز به تصمیم تو / لغو شد), live <a> URL in .ltr-block, steps, risk chips, decisions audit list, cost + UNVERIFIED, emerald «چه چیزهایی یاد گرفتی» checklist when report.learned exists, report.next_time checklist, «پروژه جدید» reload.
- Verified: node --check on app.js passes; Persian digits via faDigits() for attempt counters/step numbers; every log/command/URL/path/env-key/cost rendered through .ltr-block.

Stage Summary:
- T8 deliverables complete: web/index.html, web/app.js, web/style.css — fully offline static frontend implementing API CONTRACT v1 end-to-end (upload -> demo/zip -> assessment -> start -> SSE timeline -> approval modal -> report).
- Contract elements implemented 1:1: POST /api/upload ("file"), POST /api/sessions/demo {fixture}, GET /api/assessment (questions + expert_skip), POST answers {answers, skip_expert} -> {level}, POST start -> 202, GET snapshot {id,demo,level,status,stage_fa,events[i,type,payload,ts],plan,report,approval}, SSE /api/events/{id} with Last-Event-ID-safe manual reconnect (10-attempt cap), POST approve {approved,cost_ok,env_values}, POST level, POST cancel; all 13 SSE types handled.
- Lead must verify server-side assumptions: (1) SSE messages carry integer `id` matching snapshot event `i` (client dedupes on id <= max-i); (2) snapshot.status keyword values used for done/cancelled/approval heuristics are loose regexes (/done|success/, /cancel/, /approv/) — adjust if backend uses different status strings; (3) demo/upload responses may carry `demo:true` for the top-bar badge; (4) env_needed accepted as array of strings OR {key,value} objects; (5) report fields read defensively (url/steps/risks/decisions/estimated_cost/cost_unverified/learned/next_time + aliases).
- Not done (by design): no server run, no backend changes, no session persistence across page reload (reload during a run re-hydrates via snapshot+SSE replay only if the user re-enters the same session — currently a fresh reload returns to screen A).

---
Task ID: 2 (Phase 1 build — T1 amendment, T2..T12)
Agent: Z.ai Code (lead build engineer)
Task: Execute Phase 1 T1-T12 per Mehran's updated brief; pytest green; mock E2E in browser.

Work Log:
- Audited prior session's T1 commit; verified langgraph 1.2.16 interrupt round-trip with a live probe before writing graph code.
- Verified on docs.liara.ir + developers.liara.ir (2026-10-20): OpenAPI HTTP API (POST /v2/projects/{name}/sources, GET /v1/projects/{name}/logs, POST /v1/projects/update-envs, disks CRUD, wss shell), liara.json platform values incl. "next", disks schema {"disks":[{"name","mountTo"}]}. Recorded in config/defaults.yaml (commit c43ffb9).
- T2 inspector/rules.py R1-R6 + fixed a StopIteration bug in core._port (listen(PORT) without literal); 12 tests.
- T3 four fixtures + samples/fallback-app (plan B wired per official docs); acceptance locked in tests (next-prisma-sqlite yields R1,R2,R3,R4).
- T4 15-concept Persian glossary; T5 4-question Persian assessment + pure scoring (experience gate + expert skip); T6 LLM layer (env-config OpenAI-compat, FakeLLM deterministic Persian explain, prices.yaml all-UNVERIFIED, SecretRegister redaction, RunBudget, demo cache, JSON schema-validate with 1 retry then deterministic fallback).
- T7 LangGraph: 14 nodes, real interrupt approval gate, deterministic fix-loop cap (3) with escalation, teach_summary for beginners, MockDeployer canned failures (bad_port, missing_env_var), LiaraCliDeployer stub with verify-TODOs. DEBUG PROTOCOL exercised: found _decide() dropping node updates -> infinite loop (GraphRecursionError); one-line fix; tests green.
- T8 FastAPI app (contract v1): upload/demo/assessment/answers/start/approve/level/cancel + SSE with Last-Event-ID replay + heartbeat; worker-thread graph runner; redacting emitter; T8 UI built by frontend-styling-expert subagent (web/index.html+app.js+style.css, dark RTL Persian, demo badge, level toggle, approval modal, expired-session screen).
- T9 scripts/demo_mock.py (both levels, visibly different explanations, secrets-free reports); full pytest suite incl. API integration over TestClient.
- T10 zipsafe (zip-slip/absolute paths/count/size limits), prompt-redaction test, no-execution test. T11 SSE replay + terminal guard, cancel-during-await, 404 session_expired contract, DEMO_MODE forcing. T12 demo mode verified offline; RTL-safe .ltr-block log rendering.
- Sandbox quirk: root-level Python /app package shadows src/app in Next.js route detection (Next 404s on /). Product now serves the single exposed port directly (uvicorn on :3000); Caddyfile reverted untouched.
- Browser E2E via agent-browser on the exposed port: beginner path (report + 5-item learned checklist), expert path (terse report), broken-project -> fix#1..#3 -> escalation screen, approval modal with env inputs, mobile 375px layout, sticky footer, demo badge; no console/page errors.

Stage Summary:
- 63 pytest tests green; demo script exit 0; commits T2..T12 + sandbox commit (one per task).
- Acceptance met: next-prisma-sqlite yields R1-R4 with visibly different beginner/expert texts; approval gate blocks deploy; fix cap escalates after 3; no secrets in any emitted event.
- Deployer decision for Thursday: pure-Python LiaraApiDeployer via verified HTTP API (preferred); CLI fallback. deploy-sources payload schema still UNVERIFIED -> check paas.yaml.
- Still needs: Liara credits + real account (real deploy, prices fill), BoxAPI key (real LLM path), official docs re-check (deploy payload, CLI exit codes, releases/rollback).

---
Task ID: T13
Agent: Z.ai Code (lead build engineer)
Task: LiaraApiDeployer complete + mock-tested; OpenAPI facts recorded; DEPLOYER factory.

Work Log:
- Downloaded https://developers.liara.ir/spec/paas.yaml (54KB, openapi 3.0.1) fully and
  extracted exact shapes; recorded 3 VERIFIED facts (paas_api_auth, paas_api_endpoints,
  paas_api_error_codes) + 7 new UNVERIFIED entries in config/defaults.yaml (planID values,
  disk size units, status strings, {app}.liara.run URL format, sources semantics, disk
  mount mechanism, logs since format). URL-format check failed on docs SPA -> UNVERIFIED
  per debug protocol (did not guess).
- deployers/sources.py: build_source_zip (junk + .env excluded, prepared files override),
  describe_zip for safe logs.
- deployers/liara_api.py: Bearer httpx client; deploy = ensure_app (GET 404 -> POST create
  with LIARA_PLAN_ID, never invented) -> ensure_disks (GET + POST {name,size:str}) ->
  update-envs ({project, variables[]}) -> sources upload (multipart "file") -> poll status
  (UNVERIFIED string sets) -> url; retry/backoff on 429/5xx/timeout (our policy, flagged),
  401/404 fail fast; kind mapping auth_failed/missing_plan/rate_limited/timeout/build_failed;
  dry_run builds+validates every payload, prints redacted, sends nothing; token never logged.
- deployers/__init__.py factory DEPLOYER=mock|liara_api|liara_cli (DEMO_MODE forces mock);
  base.make_deploy_config extended (project_dir, start_command, disks, env_fixes priority);
  session.liara_token RAM-only + registered in SecretRegister on approve (never echoed).
- DEBUG PROTOCOL used twice: (1) respx same-pattern re-mock != sequence -> side_effect lists;
  (2) real bug found by tests: 401 on ensure_app mapped to unknown -> _http_fail now maps
  401 uniformly to auth_failed.
- .env.example: DEPLOYER/LIARA_API_TOKEN/LIARA_PLAN_ID/LIARA_DISK_SIZE/LIARA_DRY_RUN.

Stage Summary:
- 13 new tests (tests/test_liara_api.py) pin the client to paas.yaml BEFORE Thursday:
  success E2E, existing-app skip, 401 no-retry, missing_plan no-invent, 429 retry, 5xx cap,
  timeout, build_failed+logs, dry-run sends nothing + redacts + validates, token hygiene,
  zip exclusions, config merge priority, factory selection. Suite: 75 green.
- Thursday runbook: set LIARA_DRY_RUN=1 -> inspect payload -> unset -> live.

---
Task ID: T14
Agent: Z.ai Code (lead build engineer)
Task: Agentic fix loop — LLM diagnoses, deterministic allowlist execution, ask_user pause, cap 3.

Work Log:
- llm/diagnose.py: DiagnosisModel (pydantic; action validator raises -> client retries once
  -> fallback), ALLOWED_ACTIONS {set_env, change_port, change_start_command, add_disk,
  change_platform, ask_user, give_up}, validate_params (secret-looking set_env with non-empty
  value REJECTED -> must be ask_user; platform checked against VERIFIED liara.json values),
  apply_action (mutates plan copy; liara.json regenerated from plan), deterministic_diagnosis
  (scripted brain for FakeLLM + fallback: bad_port->change_port, missing_env_var->ask_user,
  wrong_start_script->reads project for a sibling entry file, missing_disk_sqlite->add_disk,
  unknown->give_up), build_diagnose_context (env KEYS only, log tail capped 2000 chars).
- agent/nodes.fix rewritten: LLM diagnosis -> deterministic validation (reject -> scripted
  fallback) -> apply -> emit fix event (set_env values ALWAYS masked); ask_user = LangGraph
  interrupt {kind: question, question_fa}; reply fills first missing env key and is registered
  as secret-capable; give_up -> escalate with diagnosis_fa (new AgentState.escalation_reason).
- MockDeployer v2: wrong_start_script (entry file missing in project_dir, hint lists existing
  entries) + missing_disk_sqlite (SQLite URL without disks) — both config-driven, no randomness.
- plan.disks made deterministic: build_plan_deterministic sets disks for R1; make_plan
  overrides disks like port/platform (real-LLM PlanModel dump would drop them — bug found by
  test trace).
- UI: approval modal renders question interrupts (question_fa + free-text input -> approve
  {user_reply}); .question-text CSS; node --check ok.
- main.approve: env values + liara_token registered in SecretRegister (values can never leak).
- DEBUG PROTOCOL exercised 4x: (1) respx side_effect sequences (T13 leftover), (2) test stub
  hijacked plan-building -> ScriptedLLM/CapturingClient now scope canned JSON to DiagnosisModel,
  (3) missing escalation_reason channel (LangGraph TypedDict drops undeclared keys),
  (4) leaky test double had empty logs() -> log_tail empty.

Stage Summary:
- 13 new tests (test_diagnose.py) + updated cap/bad-port/API tests: invalid action rejected,
  cap enforced via 3 ask_user rounds then escalation, ask_user pauses+reply fills env,
  give_up escalates with reason, secret set_env rejected+never leaks, log tail redacted
  before the model, mock v2 scenarios + fixes, liara.json regeneration. Suite: 88 green.
- Full agentic journey now possible offline: fail -> diagnose -> ask human -> fix -> deploy.
