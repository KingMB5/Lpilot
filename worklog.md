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
