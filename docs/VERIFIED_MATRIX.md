# VERIFIED MATRIX — live API evidence log

Every «verified» claim about the **LIVE** Liara API must trace to a row produced by
`scripts/live_check.py` below. This separates two evidence levels:

1. **Spec-level verification** — request/response shapes proven against the official OpenAPI
   snapshot (`config/spec/paas.yaml`, checked 2026-10-20) → recorded in `config/defaults.yaml`
   and `docs/METRICS_VERIFICATION.md`.
2. **Live verification** — this file. A row exists here only after a real API call was made by
   `scripts/live_check.py` (dry-run rows only PROVE what *would* be sent; they are marked
   SKIPPED, never PASS).

Verdicts: **PASS** = real 200/201 observed · **FAIL** = real error observed ·
**UNVERIFIED** = could not confirm (endpoint differed / app missing / format rejected) ·
**SKIPPED** = tier-2 step in dry-run mode (nothing sent).

Tier-3 steps (delete app / delete disk) are intentionally absent — the agent never calls them
(risk matrix, `docs/ARCHITECTURE.md` §4).

_(No live run yet — this file fills on the first execution of
`LIARA_API_TOKEN=… python scripts/live_check.py`, dry-run first.)_

---
## live_check run — 2026-10-22 18:54 UTC (mode: DRY-RUN, app: `liara-pilot-probe`)

| # | Step (endpoint) | Tier | Verdict | Detail / raw evidence |
|---|-----------------|------|---------|------------------------|
| 1 | auth+list_apps (GET /v1/projects) | 1 | PASS | token valid; 0 apps visible: [] \| raw: http 200 {"projects":[]} |
| 2 | app_details (GET /v1/projects/liara-pilot-probe) | 1 | UNVERIFIED | app 'liara-pilot-probe' does not exist yet (created in tier-2 steps) \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Not Found","data":null} |
| 3 | metrics_summary (GET /v1/projects/liara-pilot-probe/metrics/summary) | 1 | UNVERIFIED | app missing OR endpoint path differs live (spec says Reports) \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Not Found","data":null} |
| 4 | logs (GET /v1/projects/liara-pilot-probe/logs?since=...) | 1 | UNVERIFIED | app 'liara-pilot-probe' does not exist yet \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Project not found.","data":null} |
| 5 | create_app (POST /v1/projects, name=liara-pilot-probe) | 2 | SKIPPED | DRY-RUN plan (not sent): {"name": "liara-pilot-probe", "planID": "<LIARA_PLAN_ID>", "platform": "static"} \| raw: tier-2 — nothing sent without --live |
| 6 | env+disk (POST update-envs, POST disks) | 2 | SKIPPED | DRY-RUN plan (not sent): update-envs {project: liara-pilot-probe, variables: [{key: PROBE_VAR, value: ***}]}; disks {name: probe-disk, size: 1G} \| raw: tier-2 — nothing sent without --live |
| 7 | upload+build (POST /v2/projects/liara-pilot-probe/sources) | 2 | SKIPPED | DRY-RUN plan (not sent): multipart file=sources.zip (2 entries, static platform) \| raw: tier-2 — nothing sent without --live |
| 8 | restart (POST /v1/projects/liara-pilot-probe/actions/restart) | 2 | SKIPPED | restart is tier-2 — requires --live AND --approve-restart (never implicit) |

_Tier-3 (delete app/disk) is intentionally absent — the agent never calls it._

---
## live_check run — 2026-10-22 18:56 UTC (mode: LIVE, app: `liara-pilot-probe`)

| # | Step (endpoint) | Tier | Verdict | Detail / raw evidence |
|---|-----------------|------|---------|------------------------|
| 1 | auth+list_apps (GET /v1/projects) | 1 | PASS | token valid; 0 apps visible: [] \| raw: http 200 {"projects":[]} |
| 2 | app_details (GET /v1/projects/liara-pilot-probe) | 1 | UNVERIFIED | app 'liara-pilot-probe' does not exist yet (created in tier-2 steps) \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Not Found","data":null} |
| 3 | metrics_summary (GET /v1/projects/liara-pilot-probe/metrics/summary) | 1 | UNVERIFIED | app missing OR endpoint path differs live (spec says Reports) \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Not Found","data":null} |
| 4 | logs (GET /v1/projects/liara-pilot-probe/logs?since=...) | 1 | UNVERIFIED | app 'liara-pilot-probe' does not exist yet \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Project not found.","data":null} |
| 5 | create_app (POST /v1/projects, name=liara-pilot-probe) | 2 | FAIL | http 402 \| raw: http 402 {"statusCode":402,"error":"Payment Required","message":"Not enough balance.","data":null} |
| 6 | env+disk (POST update-envs, POST disks) | 2 | FAIL | update-envs: http 400 {"message":"project doesn't exists."} \| raw: http 400 {"message":"project doesn't exists."} |
| 7 | upload+build (POST /v2/projects/liara-pilot-probe/sources) | 2 | FAIL | http 404 \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"project_not_found","data":null} |
| 8 | app_details (GET /v1/projects/liara-pilot-probe) | 1 | UNVERIFIED | app 'liara-pilot-probe' does not exist yet (created in tier-2 steps) \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Not Found","data":null} |
| 9 | restart (POST /v1/projects/liara-pilot-probe/actions/restart) | 2 | SKIPPED | restart is tier-2 — requires --live AND --approve-restart (never implicit) |

_Tier-3 (delete app/disk) is intentionally absent — the agent never calls it._

---
## live_check follow-up — 2026-10-22 (root-cause of tier-2 FAILs + new real evidence)

| # | Probe | Tier | Verdict | Raw evidence |
|---|-------|------|---------|--------------|
| 1 | GET /v1/billing (billing doc outside paas.yaml) | 1 | PASS | http 200 `{"user":{"balance":0,"accountType":"REAL_PERSON","minCreditAmount":20000,"isManualMinCredit":false}}` |
| 2 | GET /v1/plans (catalog guess) | 1 | VERIFIED-NEGATIVE | http 404 `{"error":"Not Found"}` — no plan-catalog endpoint exists |
| 3 | POST /v1/projects with invalid planID (validation probe, nothing created) | 2 | PASS (validation) | http 400 `"planID" must be one of [ir-small, ir-medium, standard-base, standard-plus, pro, pro-plus, pro-max, small-g2, medium-g2, standard-base-g2, standard-plus-g2, pro-g2, pro-plus-g2, pro-max-g2, express-g2]` |
| 4 | POST /v1/projects live create (planID=ir-small) | 2 | FAIL — BLOCKED | http 402 `{"statusCode":402,"error":"Payment Required","message":"Not enough balance.","data":null}` — account balance is 0; credit voucher not redeemed / not on this account |

**Root cause of all tier-2 FAILs: account `balance: 0`.** The API token itself is
valid (tier-1 PASS). The moment credit exists on this account, re-run
`LIARA_PLAN_ID=ir-small python3 scripts/live_check.py --live` to verify
create/env/disk/upload/build/logs end-to-end. Smallest plan for test apps: `ir-small`.

**BoxAPI probe (no key held in sandbox):** GET/POST to
`https://ai.boxapi.ir/ai/v1/models|/chat/completions` → http 401 with OpenAI-shaped
error JSON (`"type":"proxy_error"`) — endpoint reachable and OpenAI-compatible;
default model switched to `gpt-5.4-mini` (operator's budget pick, 2026-10-22 —
chat-capable minis are the cheapest tier in their dashboard list); stays UNVERIFIED
until a real completion call.
