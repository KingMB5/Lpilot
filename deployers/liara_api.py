"""LiaraApiDeployer — pure-Python deploy over Liara's documented PaaS HTTP API.

Every request/response shape comes from the official OpenAPI spec
(https://developers.liara.ir/spec/paas.yaml, checked 2026-10-20; recorded as
VERIFIED facts `paas_api_auth` / `paas_api_endpoints` / `paas_api_error_codes`
in config/defaults.yaml). Anything the spec does not define is treated as
UNVERIFIED and is never invented: planID values, disk-size units, app status
strings, the live-URL format and the log `since` semantics are config inputs or
flagged best-effort behavior — see unverified_facts in config/defaults.yaml.

Deploy sequence (one deploy() call):
  1. GET  /v1/projects/{name}              -> app exists? else create (needs planID)
  2. GET  /v1/projects/{id}/disks          -> create missing disks (paid: post-approval only)
  3. POST /v1/projects/update-envs         -> env VALUES go here, nowhere else
  4. POST /v2/projects/{name}/sources      -> zip of the project (secrets excluded) -> sourceID
  5. POST /v2/projects/{name}/releases     -> {sourceID, port, type} -> releaseID (VERIFIED
     live 2026-10-22 via the official CLI: this is what actually starts the build)
  6. poll GET /v2/releases/{id}/build-logs -> release.state machine (READY | FAILED |
     UNHEALTHY | TIMEDOUT | CANCELED) + REAL build output lines (paged by createdAt)
  7. url = https://{name}.liara.run        -> VERIFIED for apps WITH a network
     (iran region without network uses {name}.iran.liara.run); the verify step's
     live URL check remains the final arbiter

Live-API additions verified 2026-10-22 (docs/VERIFIED_MATRIX.md):
- Team context: every request carries ?teamID=<LIARA_TEAM_ID> when the env var
  is set — the same mechanism as the official CLI (its setGotConfig hook sets
  the teamID search param on every URL). Credit can be TEAM-scoped: the billing
  endpoint returns {user:{balance}, team:{balance}} with the param.
- App creation additionally requires a network id (400 network_is_required) and
  accepts bundlePlanID (400: "must be one of [free, basic, standard, pro]").
  Network is resolved from LIARA_NETWORK_ID or auto-discovered (GET /v1/networks,
  first entry). The plan+price catalog lives at GET /v1/me (NOT /v1/plans -> 404).

Security (AGENTS.md):
- The API token lives only in this instance's memory. It is never logged,
  never serialized, and redacted in dry-run output.
- dry_run mode builds and validates every request payload and prints it with
  secrets redacted, without sending anything. Thursday's first real call will
  be dry-run, then live.
- Retry-with-backoff on 429/5xx/timeout (our policy); 401/404 fail fast.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

import httpx

from app.config import load_defaults
from deployers.sources import ARCHIVE_NAME, build_source_zip, describe_zip

RETRY_STATUSES = {429, 500, 502, 503, 504}

# Release state machine — VERIFIED live 2026-10-22 via the official CLI
# (src/services/build-logs.ts) + real API calls; NOT in paas.yaml.
RELEASE_OK = {"READY"}
RELEASE_BAD = {"FAILED", "UNHEALTHY", "TIMEDOUT", "CANCELED"}

# UNVERIFIED app status strings (spec does not enumerate them) — best-effort sets.
STATUS_OK = {"running", "ok", "ready", "started", "active", "online", "healthy"}
STATUS_BAD = {"failed", "error", "crashed", "crash", "exited", "dead"}

LOG_LINE_LIMIT = 200  # keep the last N lines for the self-fix loop


class DeployerConfigError(Exception):
    """Deterministic configuration failure (e.g. missing token/planID)."""


class LiaraApiDeployer:
    name = "liara-api"

    def __init__(self, api_token: str | None = None, *, base_url: str | None = None,
                 plan_id: str | None = None, disk_size: str | None = None,
                 team_id: str | None = None, network_id: str | None = None,
                 bundle_plan_id: str | None = None,
                 poll_timeout_s: float | None = None, backoff_base: float | None = None,
                 sleep: Callable[[float], None] = time.sleep, dry_run: bool | None = None) -> None:
        d = load_defaults().get("deploy", {})
        self._api_token = (api_token or os.getenv("LIARA_API_TOKEN", "")).strip()
        self.base_url = (base_url or os.getenv("LIARA_API_BASE_URL", "https://api.liara.ir")).rstrip("/")
        self.plan_id = (plan_id if plan_id is not None else os.getenv("LIARA_PLAN_ID", "")).strip()
        self.disk_size = (disk_size if disk_size is not None else os.getenv("LIARA_DISK_SIZE", "1")).strip()
        # VERIFIED live 2026-10-22: disk "size" must be a NUMBER of GB
        # (400 '"size" must be a number' on "1G"; size:1 -> 201 +
        # reservedDiskSpace: 1 on the app). Parsed once, here.
        try:
            self.disk_size_gb = int(float(self.disk_size))
        except ValueError:
            self.disk_size_gb = None
        # Team context + create-time extras (VERIFIED live 2026-10-22, see module doc).
        self.team_id = (team_id if team_id is not None else os.getenv("LIARA_TEAM_ID", "")).strip()
        self.network_id = (network_id if network_id is not None else os.getenv("LIARA_NETWORK_ID", "")).strip()
        self.bundle_plan_id = (bundle_plan_id if bundle_plan_id is not None else os.getenv("LIARA_BUNDLE_PLAN_ID", "basic")).strip()
        self.http_timeout_s = float(os.getenv("LIARA_HTTP_TIMEOUT_S", str(d.get("http_timeout_s", 30))))
        self.poll_interval_s = float(d.get("poll_interval_s", 5))
        self.poll_timeout_s = float(poll_timeout_s if poll_timeout_s is not None else d.get("timeout_s", 600))
        self.max_retries = int(d.get("max_retries", 3))
        self.backoff_base = float(backoff_base if backoff_base is not None else 0.5)
        self._sleep = sleep
        self.dry_run = (os.getenv("LIARA_DRY_RUN", "") == "1") if dry_run is None else bool(dry_run)
        self._logs: list[str] = []
        self._url: str | None = None
        self._app_name: str | None = None
        self._last_source_id: str = ""

    # ------------------------------------------------------------ HTTP core

    def _headers(self) -> dict[str, str]:
        # VERIFIED (paas_api_auth): "Authorization: Bearer <token>"
        return {"Authorization": f"Bearer {self._api_token}"}

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 files: dict | None = None, data: dict | None = None,
                 params: dict | None = None) -> httpx.Response:
        # VERIFIED live 2026-10-22: team context is a teamID QUERY param on every
        # request (mirrors the official CLI's setGotConfig searchParams hook).
        if self.team_id:
            params = {"teamID": self.team_id, **(params or {})}
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(base_url=self.base_url, timeout=self.http_timeout_s) as client:
                    resp = client.request(method, path, headers=self._headers(),
                                          json=json_body, files=files, data=data, params=params)
                if resp.status_code in RETRY_STATUSES:
                    last_exc = httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp)
                    self._log(f"http {method} {path} -> {resp.status_code}, retry {attempt}/{self.max_retries}")
                else:
                    return resp
            except httpx.TimeoutException as e:
                last_exc = e
                self._log(f"http {method} {path} -> timeout, retry {attempt}/{self.max_retries}")
            except httpx.HTTPError as e:  # connection errors etc.
                last_exc = e
                self._log(f"http {method} {path} -> {type(e).__name__}, retry {attempt}/{self.max_retries}")
            if attempt < self.max_retries:
                self._sleep(self.backoff_base * (2 ** (attempt - 1)))
        if isinstance(last_exc, httpx.TimeoutException):
            raise last_exc
        if isinstance(last_exc, httpx.HTTPStatusError):
            return last_exc.response
        raise last_exc if last_exc else RuntimeError("request loop exited unexpectedly")

    def _log(self, line: str) -> None:
        # defense in depth: never let the token leak into our own log lines
        self._logs.append(line.replace(self._api_token, "***") if self._api_token else line)

    # ------------------------------------------------------------ Deployer API

    def deploy(self, config: dict[str, Any]) -> dict:
        """config: {app_name, platform, port, env, required_env, prepared_files,
        disks?, start_command?, project_dir?} — see deployers.base.make_deploy_config."""
        self._logs = []
        app = str(config.get("app_name") or "").strip()
        if not app:
            return {"ok": False, "kind": "unknown", "message": "missing app_name", "logs": list(self._logs)}
        self._app_name = app

        if self.dry_run:
            return self._dry_run(config, app)

        if not self._api_token:
            return {"ok": False, "kind": "auth_failed", "message": "no LIARA_API_TOKEN in memory",
                    "logs": list(self._logs)}

        try:
            if (err := self._ensure_app(app, config)) is not None:
                return err
            if (err := self._ensure_disks(app, config)) is not None:
                return err
            if (err := self._update_envs(app, config)) is not None:
                return err
            if (err := self._upload_sources(app, config)) is not None:
                return err
            release_id, err = self._create_release(app, config)
            if err is not None:
                return err
            return self._await_release(release_id)
        except httpx.TimeoutException:
            return {"ok": False, "kind": "timeout", "message": "request timeout after retries",
                    "logs": list(self._logs)}
        except httpx.HTTPError as e:
            return {"ok": False, "kind": "unknown", "message": f"http error: {type(e).__name__}",
                    "logs": list(self._logs)}

    def logs(self) -> list[str]:
        """App logs (VERIFIED GET /v1/projects/{name}/logs -> [{type,datetime,message}]).
        `since` is REQUIRED by the spec (string type); its value format is
        UNVERIFIED, so it comes from LIARA_LOGS_SINCE (default "0")."""
        if not self._app_name or not self._api_token:
            return list(self._logs)
        try:
            resp = self._request("GET", f"/v1/projects/{self._app_name}/logs",
                                 params={"since": self._logs_since()})
            if resp.status_code != 200:
                self._log(f"logs -> http {resp.status_code}")
                return list(self._logs)
            items = resp.json() if isinstance(resp.json(), list) else []
            lines = [f"{it.get('datetime', '')} {it.get('type', '')}: {it.get('message', '')}".strip()
                     for it in items]
            return (list(self._logs) + lines)[-LOG_LINE_LIMIT:]
        except httpx.HTTPError as e:
            self._log(f"logs fetch failed: {type(e).__name__}")
            return list(self._logs)

    # ------------------------------------------------------------ read-only ops (T2/T6)

    def _logs_since(self) -> str:
        return os.getenv("LIARA_LOGS_SINCE", "0")

    def metrics_summary(self) -> dict | None:
        """VERIFIED endpoint GET /v1/projects/{name}/metrics/summary (no params).
        Read-only. Returns the raw body ({cpuUsage, memoryUsage, disksUsage}) or
        None on any failure — callers must label the values UNVERIFIED (units
        not yet observed; metrics_values_semantics)."""
        if not self._app_name or not self._api_token or self.dry_run:
            return None
        try:
            resp = self._request("GET", f"/v1/projects/{self._app_name}/metrics/summary")
            if resp.status_code != 200:
                self._log(f"metrics/summary -> http {resp.status_code}")
                return None
            body = resp.json()
            return body if isinstance(body, dict) else None
        except httpx.HTTPError as e:
            self._log(f"metrics fetch failed: {type(e).__name__}")
            return None

    def plans_catalog(self) -> dict | None:
        """VERIFIED live 2026-10-22: GET /v1/me returns the REAL plan catalog —
        plans.projects (RAM/CPU/volume/price-per-hour/storageClass/region/available)
        and plans.projectBundlePlans. (GET /v1/plans is 404 — plans_catalog_absent
        fact stays true for that path; the catalog lives here.) Read-only; Toman/hour
        prices enable a real cost estimate later. None on any failure."""
        if self.dry_run or not self._api_token:
            return None
        try:
            resp = self._request("GET", "/v1/me")
            if resp.status_code != 200:
                self._log(f"plans catalog (GET /v1/me) -> http {resp.status_code}")
                return None
            body = resp.json()
            plans = (body or {}).get("plans") if isinstance(body, dict) else None
            return plans if isinstance(plans, dict) else None
        except httpx.HTTPError as e:
            self._log(f"plans catalog fetch failed: {type(e).__name__}")
            return None

    def current_plan(self) -> str | None:
        """Live planID of the deployed app — VERIFIED field on GET
        /v1/projects/{name} (ProjectAllDetails.planID). The plan CATALOG with
        real prices is available via plans_catalog() (GET /v1/me, VERIFIED live
        2026-10-22); data/prices.yaml remains the offline fallback."""
        if self.dry_run or not self._app_name or not self._api_token:
            return None
        try:
            resp = self._request("GET", f"/v1/projects/{self._app_name}")
            if resp.status_code != 200:
                return None
            proj = (resp.json() or {}).get("project") or {}
            pid = proj.get("planID")
            return str(pid) if pid else None
        except httpx.HTTPError:
            return None

    def restart(self) -> dict:
        """VERIFIED endpoint POST /v1/projects/{name}/actions/restart -> 200|401|404.
        Risk tier 2 — callers MUST gate this behind explicit user approval
        (agent/risk.py); the deployer itself never asks."""
        if self.dry_run:
            self._log(f"DRY-RUN restart {self._app_name} (POST /v1/projects/{self._app_name}/actions/restart)")
            return {"ok": True, "kind": "dry_run", "message": "dry-run restart planned, nothing sent",
                    "logs": list(self._logs)}
        if not self._app_name or not self._api_token:
            return {"ok": False, "kind": "unknown", "message": "no app/token in memory", "logs": list(self._logs)}
        try:
            resp = self._request("POST", f"/v1/projects/{self._app_name}/actions/restart")
            if resp.status_code == 200:
                self._log("restart submitted")
                return {"ok": True, "kind": "success", "message": "restart submitted", "logs": list(self._logs)}
            return self._http_fail(resp, "restart")
        except httpx.HTTPError as e:
            return {"ok": False, "kind": "unknown", "message": f"restart failed: {type(e).__name__}",
                    "logs": list(self._logs)}

    def status(self) -> dict:
        """{healthy, url?} — health view for the verify step."""
        if self.dry_run or not self._app_name or not self._api_token:
            return {"healthy": False, "url": self._url}
        try:
            resp = self._request("GET", f"/v1/projects/{self._app_name}")
            if resp.status_code != 200:
                return {"healthy": False, "url": self._url}
            st = str((resp.json().get("project") or {}).get("status", "")).lower()
            return {"healthy": st in STATUS_OK, "url": self._url, "status": st}
        except httpx.HTTPError:
            return {"healthy": False, "url": self._url}

    # ------------------------------------------------------------ steps

    def _resolve_network(self) -> str:
        """Network id for app creation (VERIFIED live: 400 network_is_required).
        Order: LIARA_NETWORK_ID -> first entry of GET /v1/networks (read-only)."""
        if self.network_id:
            return self.network_id
        resp = self._request("GET", "/v1/networks")
        body = resp.json() if resp.status_code == 200 and isinstance(resp.json(), dict) else {}
        for n in body.get("networks", []):
            if isinstance(n, dict) and n.get("_id"):
                self._log(f"network auto-discovered: {n.get('name')} (id {str(n['_id'])[:8]}…)")
                return str(n["_id"])
        if resp.status_code != 200:
            self._log(f"networks list -> http {resp.status_code}")
        return ""

    def _ensure_app(self, app: str, config: dict) -> dict | None:
        resp = self._request("GET", f"/v1/projects/{app}")
        if resp.status_code == 200:
            self._log(f"app exists: {app}")
            return None
        if resp.status_code != 404:
            return self._http_fail(resp, "ensure_app")
        # 404 -> create. VERIFIED body {name, planID, platform} + live additions
        # 2026-10-22: bundlePlanID (tier name) and network (id) are required/expected.
        # planID values are real (GET /v1/me catalog) -> operator provides LIARA_PLAN_ID.
        if not self.plan_id:
            return {"ok": False, "kind": "missing_plan",
                    "message": "LIARA_PLAN_ID is required to create the app (values UNVERIFIED — see Liara console)",
                    "logs": list(self._logs)}
        network = self._resolve_network()
        if not network:
            return {"ok": False, "kind": "missing_network",
                    "message": "no network available: create one in the Liara console (or CLI 'liara network create') "
                               "or set LIARA_NETWORK_ID",
                    "logs": list(self._logs)}
        body = {"name": app, "planID": self.plan_id,
                "platform": config.get("platform") or "node",
                "bundlePlanID": self.bundle_plan_id, "network": network}
        self._log(f"create app: {app} platform={body['platform']} planID=*** bundle=*** network=***")
        resp = self._request("POST", "/v1/projects", json_body=body)
        if resp.status_code in (200, 201):
            return None
        return self._http_fail(resp, "create app")

    def _ensure_disks(self, app: str, config: dict) -> dict | None:
        disks = [d for d in (config.get("disks") or []) if str(d.get("name") or "").strip()]
        if not disks:
            return None
        resp = self._request("GET", f"/v1/projects/{app}/disks")
        existing: set[str] = set()
        if resp.status_code == 200:
            body = resp.json() if isinstance(resp.json(), dict) else {}
            existing = {str(x.get("name")) for x in body.get("disks", []) if isinstance(x, dict)}
        else:
            self._log(f"disks list -> http {resp.status_code} (creating anyway)")
        for d in disks:
            if d["name"] in existing:
                self._log(f"disk exists: {d['name']}")
                continue
            # VERIFIED live: body {name: string, size: NUMBER of GB} ("1G" -> 400).
            if self.disk_size_gb is None:
                return {"ok": False, "kind": "bad_config",
                        "message": f"LIARA_DISK_SIZE={self.disk_size!r} invalid — the live API needs a NUMBER of GB (e.g. 1)",
                        "logs": list(self._logs)}
            self._log(f"create disk: {d['name']} size={self.disk_size_gb}GB")
            resp = self._request("POST", f"/v1/projects/{app}/disks",
                                 json_body={"name": d["name"], "size": self.disk_size_gb})
            if resp.status_code not in (200, 201):
                return self._http_fail(resp, f"create disk {d['name']}")
        return None

    def _update_envs(self, app: str, config: dict) -> dict | None:
        env = config.get("env") or {}
        if not env:
            return None
        variables = [{"key": str(k), "value": str(v)} for k, v in sorted(env.items())]
        # VERIFIED body {project, variables:[{key,value}]} — VALUES go only here.
        self._log(f"update-envs: {len(variables)} variable(s) for {app}")
        resp = self._request("POST", "/v1/projects/update-envs",
                             json_body={"project": app, "variables": variables})
        # VERIFIED live 2026-10-22: this endpoint returns 201 {"message":"success"}
        # (not 200!) — accepting only 200 silently failed EVERY real deploy.
        if resp.status_code in (200, 201):
            return None
        return self._http_fail(resp, "update-envs")

    def _upload_sources(self, app: str, config: dict) -> dict | None:
        try:
            data = build_source_zip(str(config.get("project_dir") or ""),
                                    config.get("prepared_files") or [])
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "kind": "unknown", "message": f"source zip failed: {e}",
                    "logs": list(self._logs)}
        meta = describe_zip(data)
        self._log(f"upload sources: {meta['filename']} entries={meta['entries']} "
                  f"bytes={meta['bytes']} liara_json={meta['has_liara_json']}")
        # VERIFIED multipart field "file" (binary); -> 200 {sourceID}.
        # VERIFIED live 2026-10-22: the content-type/filename drive how the
        # builder extracts — application/gzip + .tar.gz is the working pair
        # (application/zip got the tar stored as *.zip and the build failed
        # extraction every time).
        resp = self._request("POST", f"/v2/projects/{app}/sources",
                             files={"file": (ARCHIVE_NAME, data, "application/gzip")})
        if resp.status_code == 200:
            body = resp.json() if isinstance(resp.json(), dict) else {}
            self._last_source_id = str(body.get("sourceID", ""))
            self._log(f"source accepted: sourceID={self._last_source_id or '?'}")
            return None
        return self._http_fail(resp, "upload sources")

    def _create_release(self, app: str, config: dict) -> tuple[str, dict | None]:
        """VERIFIED live 2026-10-22 (official CLI src/commands/deploy.ts): the
        sources upload does NOT deploy anything by itself — a RELEASE must be
        created (POST /v2/projects/{name}/releases {sourceID, port, type, build}).
        This is the step that actually starts the build."""
        # VERIFIED live 2026-10-22 (validation errors + a 201 READY build): the
        # release body needs the FULL typed shape — message non-empty, cron/args
        # arrays, envs/platformConfig objects, build.cache; gitInfo must be
        # omitted (null is rejected). App envs travel via update-envs, so the
        # release carries an empty envs map.
        body = {"sourceID": self._last_source_id,
                "port": config.get("port"),
                "type": config.get("platform") or "node",
                "message": "deployed by LiaraPilot",
                "build": {"cache": True},
                "cron": [], "args": [], "disks": [], "envs": {}, "platformConfig": {}}
        self._log(f"create release: port={body['port']} type={body['type']}")
        resp = self._request("POST", f"/v2/projects/{app}/releases", json_body=body)
        if resp.status_code in (200, 201):
            rid = str((resp.json() or {}).get("releaseID", ""))
            if rid:
                self._log(f"release created: {rid[:12]}…")
                return rid, None
            return "", {"ok": False, "kind": "unknown",
                        "message": "release response lacks releaseID", "logs": list(self._logs)}
        return "", self._http_fail(resp, "create release")

    def _await_release(self, release_id: str) -> dict:
        """Poll GET /v2/releases/{id}/build-logs until the release state machine
        resolves. VERIFIED live 2026-10-22 via the official CLI
        (src/services/build-logs.ts): buildOutput lines are the REAL build log
        (paged by createdAt as `since`); release.state is the verdict:
        READY | FAILED | UNHEALTHY | TIMEDOUT | CANCELED. STDERR lines and
        failReason land in our logs so the self-fix loop diagnoses from truth."""
        deadline = time.monotonic() + self.poll_timeout_s
        since = ""
        last_state = ""
        fail_reason = ""
        while time.monotonic() < deadline:
            resp = self._request("GET", f"/v2/releases/{release_id}/build-logs",
                                 params={"since": since} if since else None)
            if resp.status_code == 200:
                body = resp.json() if isinstance(resp.json(), dict) else {}
                release = body.get("release") or {}
                last_state = str(release.get("state") or last_state)
                fail_reason = str(release.get("failReason") or fail_reason)
                for out in body.get("buildOutput") or []:
                    stream = str(out.get("stream", "")).upper()
                    line = str(out.get("line", "")).rstrip()
                    if line:
                        self._log(f"build {stream.lower()}: {line[:200]}")
                    if out.get("createdAt"):
                        since = str(out["createdAt"])
                if last_state in RELEASE_OK:
                    break
                if last_state in RELEASE_BAD:
                    msg = f"release {last_state}" + (f": {fail_reason}" if fail_reason else "")
                    self._log(msg)
                    # UNHEALTHY = built fine but the service is not serving —
                    # the same evidence family as a failed live-URL check, so
                    # route it to the unhealthy_url diagnosis (start-command
                    # file check). FAILED/TIMEDOUT/CANCELED stay build_failed.
                    kind = "unhealthy_url" if last_state == "UNHEALTHY" else "build_failed"
                    return {"ok": False, "kind": kind, "message": msg,
                            "logs": self.logs()}
            else:
                self._log(f"build-logs -> http {resp.status_code}")
            self._sleep(self.poll_interval_s)
        else:
            return {"ok": False, "kind": "build_failed",
                    "message": f"build timeout ({self.poll_timeout_s}s), last state: {last_state or '?'}",
                    "logs": self.logs()}
        # URL format VERIFIED (CLI src/commands/deploy.ts): apps WITH a network
        # get {app}.liara.run; iran-region apps WITHOUT one use .iran.liara.run.
        # Our created apps always carry a network; the verify step's live URL
        # check remains the final arbiter.
        self._url = f"https://{self._app_name}.liara.run"
        self._log(f"deploy finished. url: {self._url}")
        return {"ok": True, "kind": "success", "message": "deployed", "url": self._url,
                "logs": list(self._logs)}

    # ------------------------------------------------------------ misc

    def _http_fail(self, resp: httpx.Response, what: str) -> dict:
        # visibility: every swallowed failure carries its status into the logs
        self._log(f"{what}: http {resp.status_code} {resp.text[:120]}")
        if resp.status_code == 401:
            return {"ok": False, "kind": "auth_failed",
                    "message": f"{what}: 401 unauthorized (check API token)", "logs": list(self._logs)}
        if resp.status_code == 429:
            return {"ok": False, "kind": "rate_limited",
                    "message": f"{what}: 429 rate limited after {self.max_retries} retries",
                    "logs": list(self._logs)}
        return {"ok": False, "kind": "unknown",
                "message": f"{what}: http {resp.status_code}", "logs": list(self._logs)}

    # ------------------------------------------------------------ dry run

    def _plan_requests(self, config: dict, app: str) -> list[dict]:
        """The exact request sequence deploy() would send, as redacted dicts.
        Shared by dry_run; the shape is validated here too."""
        env = config.get("env") or {}
        team_note = "teamID=<LIARA_TEAM_ID> on every request" if self.team_id else "no team context"
        reqs: list[dict] = [
            {"op": "ensure_app", "method": "GET", "path": f"/v1/projects/{app}",
             "headers": {"Authorization": "Bearer ***"}, "note": team_note},
        ]
        if self.team_id:
            reqs.append({"op": "resolve_network", "method": "GET", "path": "/v1/networks",
                         "note": "first team network; override with LIARA_NETWORK_ID"})
        if config.get("disks"):
            reqs.append({"op": "list_disks", "method": "GET",
                         "path": f"/v1/projects/{app}/disks",
                         "headers": {"Authorization": "Bearer ***"}})
        for d in config.get("disks") or []:
            reqs.append({"op": "create_disk", "method": "POST",
                         "path": f"/v1/projects/{app}/disks",
                         "json": {"name": str(d.get("name", "")), "size": self.disk_size_gb},
                         "note": "size = NUMBER of GB (VERIFIED live 2026-10-22)"})
        if env:
            reqs.append({"op": "update_envs", "method": "POST", "path": "/v1/projects/update-envs",
                         "json": {"project": app,
                                  "variables": [{"key": k, "value": "***"} for k in sorted(env)]},
                         "note": f"{len(env)} value(s) redacted in dry-run"})
        prepared = config.get("prepared_files") or []
        reqs.append({"op": "upload_sources", "method": "POST",
                     "path": f"/v2/projects/{app}/sources",
                     "multipart": {"file": ARCHIVE_NAME},
                     "prepared_files": [str(f.get("name")) for f in prepared]})
        reqs.append({"op": "poll_status", "method": "GET", "path": f"/v1/projects/{app}",
                     "note": f"poll every {self.poll_interval_s}s up to {self.poll_timeout_s}s"})
        reqs.append({"op": "logs", "method": "GET", "path": f"/v1/projects/{app}/logs",
                     "note": "since param format UNVERIFIED — omitted"})
        return reqs

    def _validate_plan(self, reqs: list[dict], config: dict) -> list[str]:
        """Deterministic payload validation before Thursday's first live call."""
        problems: list[str] = []
        app = self._app_name or ""
        if not app or any(c in app for c in "/ \t"):
            problems.append("app_name invalid (must be a bare project name)")
        if not self._api_token:
            problems.append("no API token in memory (approve flow or LIARA_API_TOKEN)")
        if not self.plan_id:
            problems.append("LIARA_PLAN_ID empty — app creation would be refused (values from GET /v1/me catalog)")
        if self.team_id and not self.network_id:
            problems.append("LIARA_NETWORK_ID empty — creation will auto-discover the first team "
                            "network (fails with a clear error if none exists)")
        if not config.get("project_dir"):
            problems.append("no project_dir in deploy config — sources upload impossible")
        platform = config.get("platform")
        allowed = set(load_defaults().get("deploy", {}).get("platform_values", []))
        if platform not in allowed:
            problems.append(f"platform '{platform}' not in verified liara.json values {sorted(allowed)}")
        port = config.get("port")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            problems.append(f"port {port!r} invalid")
        for r in reqs:
            if r.get("json") is not None and not isinstance(r["json"], dict):
                problems.append(f"{r['op']}: json body must be an object")
        return problems

    def _dry_run(self, config: dict, app: str) -> dict:
        reqs = self._plan_requests(config, app)
        problems = self._validate_plan(reqs, config)
        self._log(f"DRY-RUN for app {app} — {len(reqs)} request(s) planned, nothing sent")
        for r in reqs:
            self._log("DRY-RUN " + json.dumps(r, ensure_ascii=False))
        for p in problems:
            self._log(f"DRY-RUN PROBLEM: {p}")
        zip_meta: dict | None = None
        if config.get("project_dir"):
            try:
                zip_meta = describe_zip(build_source_zip(str(config["project_dir"]),
                                                         config.get("prepared_files") or []))
                self._log(f"DRY-RUN artifact: {json.dumps(zip_meta, ensure_ascii=False)}")
            except (OSError, ValueError) as e:
                self._log(f"DRY-RUN PROBLEM: artifact build failed: {e}")
                problems.append("source artifact build failed")
        ok = not problems
        return {"ok": ok, "kind": "dry_run" if ok else "dry_run_failed",
                "message": "dry-run: all payloads validated" if ok else "dry-run found problems",
                "problems": problems, "requests": reqs, "artifact": zip_meta,
                "url": None, "logs": list(self._logs)}
