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

**BoxAPI probe (historical, before a key existed):** GET/POST to
`https://ai.boxapi.ir/ai/v1/models|/chat/completions` → http 401 with OpenAI-shaped
error JSON (`"type":"proxy_error"`) — endpoint reachable and OpenAI-compatible.

---
## BoxAPI real-LLM verification — 2026-10-22 (operator key used in-session, never stored)

| # | Probe | Verdict | Raw evidence |
|---|-------|---------|--------------|
| 1 | POST /v1/chat/completions, model=gpt-5.4-mini, `max_tokens:20` | FAIL (param) | http 400 `{"error":{"message":"Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.","type":"invalid_request_error"}}` — key AUTHENTICATED (past 401), newer-OpenAI param shape |
| 2 | same, `max_completion_tokens:20` | **PASS** | http 200, `choices[0].message.content == "PONG"`, `model:"gpt-5.4-…"`, OpenAI-shaped JSON with usage/content-filter fields |
| 3 | OUR client end-to-end (`llm/client.py` OpenAICompatClient, temperature=0.2, DEFAULT base+model) | **PASS** | meaningful deployability answer: "No — without README, Dockerfile, or tests, deployment is too undocumented and unverified." ; call_log: `call#1 model=gpt-5.4-mini chars=162` |
| 4 | FULL product golden path with real LLM (uvicorn DEMO_MODE=0 DEPLOYER=mock, broken-project fixture) | **PASS** | upload → assessment (beginner 5/10) → LLM Persian plan (4 steps, port 3000, platform node, env_needed=[MISSING_TOKEN, PORT], liara.json prepared) → tier-2 approval gate → approve + env values → deploy (mock URL, Liara blocked — see 402 rows above) → verify → report with real risk R6_missing_env_vars (Persian detail); session `llm_name: openai-compat` throughout |
| 5 | INFO-log replay through our client | **PASS** | `llm call #1 (model=gpt-5.4-mini, 47 chars) — prompt content never logged`; Persian reply «سالمم، ممنون!» |

**Compatibility note (real-call discovery):** `gpt-5.4-mini` rejects the legacy
`max_tokens` parameter (requires `max_completion_tokens`). LiaraPilot's client
sends **neither** (`model`, `messages`, `temperature=0.2` only) → compatible as-is.
If a future switch adds token caps, use `max_completion_tokens`.

**Deployer-honesty note:** row 4's deploy leg used MockDeployer because every
live app creation is blocked by the account's zero balance (rows above). The
LLM leg was REAL end-to-end. This split will be re-run 100% real the moment
credit is redeemed on the token's account.

---
## live_check run — 2026-10-22 19:25 UTC (mode: LIVE, app: `liara-pilot-probe`)

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
| 9 | restart (POST /v1/projects/liara-pilot-probe/actions/restart) | 2 | FAIL | http 404 \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Project not found.","data":null} |

_Tier-3 (delete app/disk) is intentionally absent — the agent never calls it._

---
## live_check run — 2026-10-22 19:45 UTC (mode: LIVE, app: `liara-pilot-probe`)

| # | Step (endpoint) | Tier | Verdict | Detail / raw evidence |
|---|-----------------|------|---------|------------------------|
| 1 | auth+list_apps (GET /v1/projects) | 1 | PASS | token valid; 0 apps visible: [] \| raw: http 200 {"projects":[]} |
| 2 | plans_catalog (GET /v1/me — real plan+price catalog) | 1 | PASS | catalog live: 15 available iran plans; cheapest=small-g2 (659.72 Toman/h) — real prices, replaces 404 /v1/plans \| raw: http 200 {"user":{"isTeamCreator":true,"_id":"[REDACTED-USERID]","fullname":"[REDACTED-PII]","nationalCode":"[REDACTED-PII]","invoiceIssuingEnabled":true,"email":"[REDACTED-PII]","phone":"[REDACTED-PII]","joined_at":"2026-05-16T14:47:07.366Z","freeCreditTime":-129,"hasPassword":true,"emailVerifiedAt":"202 |
| 3 | app_details (GET /v1/projects/liara-pilot-probe) | 1 | UNVERIFIED | app 'liara-pilot-probe' does not exist yet (created in tier-2 steps) \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Not Found","data":null} |
| 4 | metrics_summary (GET /v1/projects/liara-pilot-probe/metrics/summary) | 1 | UNVERIFIED | app missing OR endpoint path differs live (spec says Reports) \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Not Found","data":null} |
| 5 | logs (GET /v1/projects/liara-pilot-probe/logs?since=...) | 1 | UNVERIFIED | app 'liara-pilot-probe' does not exist yet \| raw: http 404 {"statusCode":404,"error":"Not Found","message":"Project not found.","data":null} |
| 6 | create_app (POST /v1/projects, name=liara-pilot-probe) | 2 | PASS | app created (planID=small-g2, bundle=basic, network resolved) \| raw: http 200 {"projectID":"6ada67d0aa8f2ba374d04af9"} |
| 7 | env+disk (POST update-envs, POST disks) | 2 | UNVERIFIED | envs ok; disk create: http 400 {"statusCode":400,"error":"Bad Request","message":"\"size\" must be a number"} (size units UNVERIFIED) \| raw: http 400 {"statusCode":400,"error":"Bad Request","message":"\"size\" must be a number"} |
| 8 | upload+build (POST /v2/projects/liara-pilot-probe/sources) | 2 | PASS | source accepted sourceID=a3ff4536-0fd5-4f64-8b2a-1130d3c85a45.zip \| raw: http 200 {"sourceID":"a3ff4536-0fd5-4f64-8b2a-1130d3c85a45.zip"} |
| 9 | app_details (GET /v1/projects/liara-pilot-probe) | 1 | PASS | status='ACTIVE' planID='small-g2' platform=None \| raw: http 200 {"project":{"_id":"6ada67d0aa8f2ba374d04af9","project_id":"liara-pilot-probe","type":"static","status":"ACTIVE","readOnlyRootFilesystem":true,"defaultSubdomain":true,"defaultSubdomainExpired":false,"keepDefaultSubdomain":false,"zeroDowntime":false,"scale":1,"envs":[{"key":"PROBE_VAR","value":"liarap |
| 10 | restart (POST /v1/projects/liara-pilot-probe/actions/restart) | 2 | PASS | restart submitted \| raw: http 200  |

_Tier-3 (delete app/disk) is intentionally absent — the agent never calls it._

---
## The deploy-pipeline saga — 2026-10-22 19:30→21:30 UTC (all raw evidence)

The first 100% real product deploy surfaced a chain of API realities the spec
snapshot (paas.yaml, 2026-10-20) does not describe. Every item below is a real
call/build/release; the official CLI source (liara-cloud/cli) corroborates each.

| # | Discovery | Verdict | Raw evidence |
|---|-----------|---------|--------------|
| 1 | Team credit is invisible without context: every request needs `?teamID=` (CLI: setGotConfig searchParams hook) | **PASS** | billing → `{user:{balance:0}, team:{balance:2000000}}`; without it create → 402 despite team credit |
| 2 | `POST /v1/projects/update-envs` returns **201** (not 200) | **PASS** | live 201 `{"message":"success"}`; the 200-only check silently failed EVERY real deploy (3 fix attempts burned, session 4721d9f371d2) |
| 3 | App creation requires `network` id + `bundlePlanID` tier | **PASS** | 400 `network_is_required`; 400 `"bundlePlanID" must be one of [free, basic, standard, pro]`; then 200 `{projectID}` |
| 4 | Sources upload alone deploys NOTHING — a RELEASE must be created | **PASS** | CLI deploy.ts: `POST /v2/projects/{name}/releases {sourceID, port, type, build…}` → `{releaseID}`; body validation: message non-empty, cron/args arrays, envs/platformConfig objects, gitInfo omitted |
| 5 | Build observation = `GET /v2/releases/{id}/build-logs?since=<last createdAt>` | **PASS** | `{release:{state,failReason}, buildOutput:[{stream,line,createdAt}]}`; states READY/FAILED/UNHEALTHY/TIMEDOUT/CANCELED; REAL build lines (npm install 68 pkgs, image push 100%) |
| 6 | Sources artifact = **gzip tar** (not zip), multipart `application/gzip` + `.tar.gz` name | **PASS** | zip + tar-with-`application/zip` → release FAILED "An error occurred during extraction"; `application/gzip` → build READY with our bytes (release 6ada77b6) |
| 7 | App status ACTIVE ≠ deployed: `isDeployed` field is the truth | **PASS** | project ACTIVE with isDeployed=false for 20+ min while unbuilt; CLI deploys flip it true |
| 8 | URL format for apps WITH a network: `https://{app}.liara.run` | **PASS** | CLI deploy.ts + live: `curl https://liarapilot-app.liara.run` → 200 "never happens in mock" after the self-fix |
| 9 | Docker platform: RUN steps have NO PyPI/mirror access in the iran builder | **FAIL (platform)** | 3 docker releases FAILED silently at `RUN pip install` (direct, runflare mirror, offline wheels at COPY) — buildpacks use an internal mirror `http://10.16.100.42/repository/pypi-proxy` |
| 10 | Python buildpack: pip works (internal mirror), release reaches READY, but a raw FastAPI Procfile app does not serve (502) and container runtime logs are NOT exposed via `GET /v1/projects/{name}/logs` | **UNVERIFIED** | release 6ada7ea5 READY; URL 502; logs endpoint returns only the build-agent notice |

### The money scene — 100% REAL closed loop (session cdbe0871e9db, run 21:02-21:07 UTC)

```
upload broken-project.zip → assessment (beginner 5/10) → real LLM plan (gpt-5.4-mini)
→ tier-2 approval (env values) → create app → envs (201!) → tar.gz upload
→ release → build (npm install, image push 100%) → READY
→ verify: REAL GET https://liarapilot-app.liara.run → 502   [caught!]
→ fix: evidence-based diagnosis (start «npm run start» → node missing-entry.js
   absent; «node server.js» exists) → change_start_command → package.json override
→ redeploy → release READY
→ verify: REAL GET → 200 ✓
→ report: deployed + fix_history audit trail
OUTSIDE CHECK: curl https://liarapilot-app.liara.run → 200 "never happens in mock"
```

Test apps deleted afterwards (liarapilot-app, liarapilot-agent — projects: []).
