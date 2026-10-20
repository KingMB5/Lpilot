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
