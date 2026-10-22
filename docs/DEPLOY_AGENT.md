# Deploying LiaraPilot itself (T16 — prepared, NOT yet deployed)

The agent is a plain Python/FastAPI app, so it can run on Liara's `python`
platform (platform value VERIFIED — config/defaults.yaml `liara_json_platform_values`).
This file lists everything that is ready and everything that is still UNVERIFIED.

## What is ready

| Artifact | Purpose |
|---|---|
| `liara.json` | `{"platform": "python", "port": 3000}` — minimal, only VERIFIED fields |
| `requirements.txt` | pinned runtime deps (mirrors `pyproject.toml`) |
| `GET /api/health` | healthcheck JSON: version, demo_mode, deployer, llm_configured, uptime — no secrets |
| `web/` static UI | served by FastAPI itself, no build step |

## Production run command

```
uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Liara's `port` env convention: the app binds the port declared in `liara.json`
(3000). If Liara injects a different `PORT` env var at runtime, honor it —
**UNVERIFIED** (see below).

## Required environment variables

| Variable | Required | Notes |
|---|---|---|
| `DEMO_MODE` | no | `1` forces FakeLLM+MockDeployer (offline demo) |
| `DEPLOYER` | for real runs | `mock` (default) \| `liara_api` \| `liara_cli` |
| `LIARA_API_TOKEN` | for `liara_api` | the OPERATOR's token; RAM-only per session, never logged |
| `LIARA_PLAN_ID` | to CREATE new apps | values UNVERIFIED — copy from Liara console |
| `LIARA_DISK_SIZE` | if disks are created | NUMBER of GB (VERIFIED live), default `1` |
| `LIARA_DRY_RUN` | recommended first run | `1` = validate+print payloads, send nothing |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | for real LLM | BoxAPI or any OpenAI-compatible endpoint; empty → FakeLLM |

Security posture (AGENTS.md): the app stores nothing on disk; sessions live in
RAM and die on restart; uploaded sources are zipped (never executed); every
LLM prompt and event passes the SecretRegister.

## UNVERIFIED items (do NOT assume — verify on the real deploy)

1. `python_platform_build_run` — how Liara's Python buildpack detects build/run
   commands (requirements.txt? Procfile? liara.json fields?). Not in paas.yaml
   or the docs pages we verified. First real deploy answers this.
2. `liara_json_healthcheck_field` — whether liara.json supports a healthCheck
   declaration and its schema. `/api/health` exists regardless.
3. `port_env_var` — whether Liara injects `PORT` and expects the app to bind it.
4. Python version availability (we pin `>=3.12` locally; the platform's runtime
   selection mechanism is UNVERIFIED).
5. Everything in `config/defaults.yaml → unverified_facts` (pricing, plan IDs,
   disk size units, app status strings, `{app}.liara.run` URL format, sources
   semantics, disk mounting, logs `since` format).

## Thursday runbook (first real deploy of the agent)

1. `LIARA_DRY_RUN=1` + a target app → read the printed payloads.
2. Unset dry-run, deploy the agent, `curl /api/health`.
3. Only then run a user-project deploy through the UI.
