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
