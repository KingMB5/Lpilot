"""T13 — LiaraApiDeployer contract tests against recorded HTTP fixtures (respx).

Every request/response shape mirrors the official OpenAPI spec (paas.yaml,
checked 2026-10-20 — see config/defaults.yaml). These tests pin our client to
that contract BEFORE any real call happens (dry-run first, then live Thursday).
"""

from __future__ import annotations

import io
import json
import pathlib
import tempfile
import tarfile

import httpx
import pytest
import respx

from deployers import make_deployer
from deployers.base import make_deploy_config
from deployers.liara_api import LiaraApiDeployer
from deployers.sources import build_source_zip, describe_zip

BASE = "https://api.liara.ir"
TOKEN = "liara-token-abc123"
PLAN = "g2"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DEMO_MODE", "DEPLOYER", "LIARA_API_TOKEN", "LIARA_PLAN_ID",
                "LIARA_DISK_SIZE", "LIARA_DRY_RUN", "LIARA_API_BASE_URL",
                "LIARA_TEAM_ID", "LIARA_NETWORK_ID", "LIARA_BUNDLE_PLAN_ID"):
        monkeypatch.delenv(var, raising=False)


def deployer(**kw) -> LiaraApiDeployer:
    kw.setdefault("api_token", TOKEN)
    kw.setdefault("plan_id", PLAN)
    kw.setdefault("backoff_base", 0)
    kw.setdefault("sleep", lambda _s: None)
    return LiaraApiDeployer(**kw)


def tmp_project() -> str:
    d = pathlib.Path(tempfile.mkdtemp())
    d.joinpath("index.js").write_text("console.log(1)", encoding="utf-8")
    return str(d)


def _cfg(project_dir: str, **over) -> dict:
    base = {
        "app_name": "my-app",
        "platform": "node",
        "port": 3000,
        "env": {"ADMIN_PASSWORD": "s3cret-value"},
        "required_env": ["ADMIN_PASSWORD"],
        "prepared_files": [{"name": "liara.json", "content": '{"platform":"node","port":3000}'}],
        "disks": [],
        "start_command": "npm start",
        "project_dir": project_dir,
    }
    base.update(over)
    return base


def _zip_from_multipart(body: bytes) -> bytes:
    """Extract the sources.tar.gz payload from a multipart/form-data request body."""
    marker = b'filename="sources.tar.gz"'
    i = body.find(marker)
    assert i != -1, "multipart body lacks sources.tar.gz part"
    head = body.find(b"\r\n\r\n", i) + 4
    tail = body.find(b"\r\n--", head)
    return body[head:tail]


# ---------------------------------------------------------------- happy paths

@respx.mock
def test_success_flow_creates_app_disk_envs_and_uploads(tmp_path):
    get_app = respx.get(f"{BASE}/v1/projects/my-app").mock(side_effect=[
        httpx.Response(404),   # ensure_app: does not exist
        httpx.Response(200, json={"project": {"project_id": "my-app",
                                              "envs": [{"key": "OLD_VAR", "value": "keep-me"}]}}),
    ])
    create = respx.post(f"{BASE}/v1/projects").mock(return_value=httpx.Response(200))
    # network auto-discovery (VERIFIED live 2026-10-22: creation needs a network id)
    respx.get(f"{BASE}/v1/networks").mock(return_value=httpx.Response(200, json={
        "networks": [{"_id": "net-123", "name": "team-net"}]}))
    respx.get(f"{BASE}/v1/projects/my-app/disks").mock(
        return_value=httpx.Response(200, json={"disks": [], "mounts": []}))
    disk = respx.post(f"{BASE}/v1/projects/my-app/disks").mock(return_value=httpx.Response(201))
    envs = respx.post(f"{BASE}/v1/projects/update-envs").mock(
        return_value=httpx.Response(200, json={"message": "ok"}))
    upload = respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "src-1"}))
    # release flow (VERIFIED live 2026-10-22: sources upload alone deploys nothing)
    release = respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-1"}))
    build_logs = respx.get(f"{BASE}/v2/releases/rel-1/build-logs").mock(side_effect=[
        httpx.Response(200, json={"release": {"state": "BUILDING"},
                                  "buildOutput": [{"stream": "STDOUT", "line": "npm install",
                                                   "createdAt": "2026-10-22T20:00:00Z"}]}),
        httpx.Response(200, json={"release": {"state": "READY"}, "buildOutput": []}),
    ])

    res = deployer().deploy(_cfg(tmp_project(), disks=[{"name": "database", "mountTo": "db"}]))

    assert res["ok"] is True and res["kind"] == "success"
    assert res["url"] == "https://my-app.liara.run"
    assert get_app.call_count == 2  # existence check + read-current-env for merge
    assert release.call_count == 1
    rel_body = json.loads(release.calls.last.request.content)
    assert rel_body["sourceID"] == "src-1" and rel_body["port"] == 3000
    # VERIFIED live shape: typed collections + non-empty message, no gitInfo
    assert rel_body["cron"] == [] and rel_body["envs"] == {} and rel_body["platformConfig"] == {}
    assert rel_body["message"] and "gitInfo" not in rel_body
    assert build_logs.call_count == 2  # BUILDING -> READY
    # create body matches the VERIFIED schema (+ live 2026-10-22 additions:
    # bundlePlanID tier name + network id, auto-discovered here)
    create_body = json.loads(create.calls.last.request.content)
    assert create_body == {"name": "my-app", "planID": PLAN, "platform": "node",
                           "bundlePlanID": "basic", "network": "net-123"}
    # build-logs pagination uses the last line's createdAt as `since` (CLI contract)
    assert str(build_logs.calls.last.request.url.params.get("since")) == "2026-10-22T20:00:00Z"
    # disk body: NUMBER of GB (VERIFIED live 2026-10-22 — "1G" is rejected)
    assert json.loads(disk.calls.last.request.content) == {"name": "database", "size": 1}
    # env VALUES go to update-envs and nowhere else; VERIFIED live 2026-10-24:
    # update-envs REPLACES the whole env list, so the deployer merges first —
    # existing app variables survive (config wins on conflicts).
    env_body = json.loads(envs.calls.last.request.content)
    assert env_body["project"] == "my-app"
    assert {"key": "ADMIN_PASSWORD", "value": "s3cret-value"} in env_body["variables"]
    assert {"key": "OLD_VAR", "value": "keep-me"} in env_body["variables"]
    # upload is multipart field "file"; zip carries liara.json + sources
    req = upload.calls.last.request
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    with tarfile.open(fileobj=io.BytesIO(_zip_from_multipart(req.content)), mode="r:gz") as tf:
        names = tf.getnames()
    assert "liara.json" in names and "index.js" in names


@respx.mock
def test_existing_app_skips_create():
    get_app = respx.get(f"{BASE}/v1/projects/existing").mock(return_value=httpx.Response(
        200, json={"project": {"project_id": "existing", "status": "ACTIVE", "isDeployed": True}}))
    respx.post(f"{BASE}/v2/projects/existing/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.post(f"{BASE}/v2/projects/existing/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-x"}))
    respx.get(f"{BASE}/v2/releases/rel-x/build-logs").mock(
        return_value=httpx.Response(200, json={"release": {"state": "READY"}, "buildOutput": []}))
    create = respx.post(f"{BASE}/v1/projects").mock(return_value=httpx.Response(200))

    res = deployer().deploy({"app_name": "existing", "platform": "node", "port": 3000,
                             "env": {}, "required_env": [], "prepared_files": [],
                             "disks": [], "project_dir": tmp_project()})
    assert res["ok"] is True and res["kind"] == "success"
    assert create.call_count == 0  # app existed — never re-created
    assert get_app.call_count == 1  # existence check only


@respx.mock
def test_update_envs_accepts_201_not_only_200(tmp_path):
    """VERIFIED live 2026-10-22: POST update-envs returns 201 — accepting only
    200 silently failed every real deploy (3 fix attempts burned on it)."""
    respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(200, json={"project": {"status": "ACTIVE", "isDeployed": True}}))
    envs = respx.post(f"{BASE}/v1/projects/update-envs").mock(
        return_value=httpx.Response(201, json={"message": "success"}))
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-1"}))
    respx.get(f"{BASE}/v2/releases/rel-1/build-logs").mock(
        return_value=httpx.Response(200, json={"release": {"state": "READY"}, "buildOutput": []}))
    res = deployer().deploy(_cfg(tmp_project()))
    assert res["ok"] is True
    assert envs.call_count == 1


# ---------------------------------------------------------------- team context (VERIFIED live 2026-10-22)

@respx.mock
def test_team_id_travels_as_query_param_on_every_request():
    """The official CLI sets teamID on every URL (setGotConfig hook); our
    deployer mirrors that with LIARA_TEAM_ID / team_id=."""
    get_app = respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(200, json={"project": {"project_id": "my-app", "status": "ACTIVE", "isDeployed": True}}))
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    release = respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-1"}))
    respx.get(f"{BASE}/v2/releases/rel-1/build-logs").mock(
        return_value=httpx.Response(200, json={"release": {"state": "READY"}, "buildOutput": []}))
    res = deployer(team_id="team-42").deploy({"app_name": "my-app", "platform": "node", "port": 3000,
                                              "env": {}, "required_env": [], "prepared_files": [],
                                              "disks": [], "project_dir": tmp_project()})
    assert res["ok"] is True
    assert get_app.call_count >= 1 and release.call_count == 1
    for call in get_app.calls:  # EVERY request carried the team param
        assert call.request.url.params["teamID"] == "team-42"
    for call in release.calls:
        assert call.request.url.params["teamID"] == "team-42"


@respx.mock
def test_missing_network_blocks_create_with_clear_error():
    respx.get(f"{BASE}/v1/projects/my-app").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/v1/networks").mock(return_value=httpx.Response(200, json={"networks": []}))
    create = respx.post(f"{BASE}/v1/projects").mock(return_value=httpx.Response(200))
    res = deployer().deploy({"app_name": "my-app", "platform": "node", "port": 3000,
                             "env": {}, "required_env": [], "prepared_files": [],
                             "disks": [], "project_dir": tmp_project()})
    assert res["ok"] is False and res["kind"] == "missing_network"
    assert "network" in res["message"].lower()
    assert create.call_count == 0  # never invent a network


@respx.mock
def test_explicit_network_id_skips_discovery():
    get_app = respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(404))
    discovery = respx.get(f"{BASE}/v1/networks").mock(return_value=httpx.Response(200, json={"networks": []}))
    create = respx.post(f"{BASE}/v1/projects").mock(return_value=httpx.Response(200))
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-1"}))
    respx.get(f"{BASE}/v2/releases/rel-1/build-logs").mock(
        return_value=httpx.Response(200, json={"release": {"state": "READY"}, "buildOutput": []}))
    res = deployer(network_id="net-explicit").deploy(
        {"app_name": "my-app", "platform": "node", "port": 3000, "env": {}, "required_env": [],
         "prepared_files": [], "disks": [], "project_dir": tmp_project()})
    assert res["ok"] is True
    assert discovery.call_count == 0  # env-provided id wins, no discovery call
    assert json.loads(create.calls.last.request.content)["network"] == "net-explicit"


@respx.mock
def test_plans_catalog_reads_v1_me():
    """VERIFIED live 2026-10-22: the real plan+price catalog is GET /v1/me
    (NOT /v1/plans, which stays 404)."""
    respx.get(f"{BASE}/v1/me").mock(return_value=httpx.Response(200, json={
        "plans": {"projects": {"small-g2": {"RAM": {"amount": 0.5}, "price": 659.72,
                                            "region": "iran", "available": True}},
                  "projectBundlePlans": {"small-g2": {"basic": {"available": True}}}}}))
    d = deployer()
    d._app_name = "my-app"
    cat = d.plans_catalog()
    assert cat and "small-g2" in cat["projects"]
    assert cat["projects"]["small-g2"]["price"] == 659.72


# ---------------------------------------------------------------- auth / errors

@respx.mock
def test_401_fails_fast_without_retry():
    route = respx.get(f"{BASE}/v1/projects/my-app").mock(return_value=httpx.Response(401))
    res = deployer().deploy(_cfg("/tmp"))
    assert res["ok"] is False and res["kind"] == "auth_failed"
    assert route.call_count == 1  # no retry on auth errors


@respx.mock
def test_missing_plan_blocks_create_without_inventing():
    respx.get(f"{BASE}/v1/projects/my-app").mock(return_value=httpx.Response(404))
    create = respx.post(f"{BASE}/v1/projects").mock(return_value=httpx.Response(200))
    res = deployer(plan_id="").deploy(_cfg("/tmp"))
    assert res["ok"] is False and res["kind"] == "missing_plan"
    assert "UNVERIFIED" in res["message"]
    assert create.call_count == 0  # never invent a plan


@respx.mock
def test_429_retried_with_backoff_then_success():
    route = respx.post(f"{BASE}/v1/projects/update-envs").mock(side_effect=[
        httpx.Response(429), httpx.Response(429), httpx.Response(201, json={"message": "success"})])
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(200, json={"project": {"status": "ACTIVE", "isDeployed": True}}))
    respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-1"}))
    respx.get(f"{BASE}/v2/releases/rel-1/build-logs").mock(
        return_value=httpx.Response(200, json={"release": {"state": "READY"}, "buildOutput": []}))

    res = deployer().deploy(_cfg(tmp_project()))
    assert res["ok"] is True
    assert route.call_count == 3  # 429, 429, then success


@respx.mock
def test_5xx_gives_up_after_max_retries():
    route = respx.post(f"{BASE}/v2/projects/my-app/sources").mock(return_value=httpx.Response(500))
    respx.get(f"{BASE}/v1/projects/my-app").mock(return_value=httpx.Response(
        200, json={"project": {"status": "running"}}))
    respx.post(f"{BASE}/v1/projects/update-envs").mock(
        return_value=httpx.Response(200, json={"message": "ok"}))

    d = deployer()
    res = d.deploy(_cfg(tmp_project()))
    assert res["ok"] is False and res["kind"] == "unknown"
    assert "500" in res["message"]
    assert route.call_count == d.max_retries


@respx.mock
def test_timeout_maps_to_timeout_kind():
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(side_effect=httpx.ConnectTimeout("boom"))
    respx.get(f"{BASE}/v1/projects/my-app").mock(return_value=httpx.Response(
        200, json={"project": {"status": "running"}}))
    respx.post(f"{BASE}/v1/projects/update-envs").mock(
        return_value=httpx.Response(200, json={"message": "ok"}))

    res = deployer().deploy(_cfg(tmp_project()))
    assert res["ok"] is False and res["kind"] == "timeout"


@respx.mock
def test_build_failed_status_returns_logs_for_fix_loop():
    """The verdict comes from the release state machine; both the app's /logs
    endpoint and the release buildOutput land in the fix loop's evidence."""
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.get(f"{BASE}/v1/projects/my-app").mock(return_value=httpx.Response(
        200, json={"project": {"status": "ACTIVE", "isDeployed": True}}))
    respx.post(f"{BASE}/v1/projects/update-envs").mock(
        return_value=httpx.Response(200, json={"message": "ok"}))
    respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-f"}))
    respx.get(f"{BASE}/v1/projects/my-app/logs").mock(return_value=httpx.Response(
        200, json=[{"type": "error", "datetime": "2026-10-20", "message": "npm ERR! missing script"}]))
    respx.get(f"{BASE}/v2/releases/rel-f/build-logs").mock(
        return_value=httpx.Response(200, json={"release": {"state": "FAILED", "failReason": "exit 1"},
                                               "buildOutput": []}))

    res = deployer().deploy(_cfg(tmp_project()))
    assert res["ok"] is False and res["kind"] == "build_failed"
    assert any("npm ERR!" in line for line in res["logs"])
    assert "FAILED" in res["message"]


# ---------------------------------------------------------------- dry run

@respx.mock
def test_dry_run_validates_payloads_without_sending(tmp_path):
    catch_all = respx.route().mock(return_value=httpx.Response(200, json={}))

    res = deployer(dry_run=True).deploy(_cfg(tmp_project(), disks=[{"name": "database", "mountTo": "db"}]))

    assert res["ok"] is True and res["kind"] == "dry_run"
    assert res["problems"] == []
    assert catch_all.call_count == 0          # NOTHING was sent
    blob = "\n".join(res["logs"])
    assert TOKEN not in blob                  # token redacted
    assert "s3cret-value" not in blob         # env values redacted
    assert '"value": "***"' in blob
    assert res["artifact"]["has_liara_json"] is True
    # dry-run catches a bad config deterministically
    res2 = deployer(dry_run=True, plan_id="").deploy(_cfg(tmp_project()))
    assert res2["kind"] == "dry_run_failed"
    assert any("LIARA_PLAN_ID" in p for p in res2["problems"])


# ---------------------------------------------------------------- secret hygiene

@respx.mock
def test_token_never_appears_in_deployer_logs():
    respx.get(f"{BASE}/v1/projects/my-app").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/v1/networks").mock(return_value=httpx.Response(200, json={
        "networks": [{"_id": "net-1", "name": "n"}]}))
    respx.post(f"{BASE}/v1/projects").mock(return_value=httpx.Response(401))
    respx.get(f"{BASE}/v1/projects/my-app/logs").mock(return_value=httpx.Response(401))
    d = deployer()
    res = d.deploy(_cfg("/tmp"))
    assert res["ok"] is False
    blob = "\n".join(res["logs"]) + "\n".join(d.logs())
    assert TOKEN not in blob


# ---------------------------------------------------------------- zip + config helpers

def test_source_zip_excludes_secrets_and_junk(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("junk", encoding="utf-8")
    (tmp_path / ".env").write_text("ADMIN_PASSWORD=super-secret-1", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('app')", encoding="utf-8")
    (tmp_path / "cache.log").write_text("log", encoding="utf-8")
    data = build_source_zip(str(tmp_path), [{"name": "liara.json", "content": "{}"}])
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        names = set(tf.getnames())
    assert names == {"app.js", "liara.json"}
    meta = describe_zip(data)
    assert meta["entries"] == 2 and meta["has_liara_json"] is True


def test_make_deploy_config_merges_env_fixes_over_approvals():
    inspection = {"platform": "node", "env_vars_required": ["X"], "start_command": "npm start"}
    plan = {"app_name": "a", "port": 3000, "env_placeholders": {"X": ""},
            "env_fixes": {"X": "from-agent"}, "disks": [{"name": "d", "mountTo": "db"}]}
    cfg = make_deploy_config(inspection, plan, {"env_values": {"X": "from-user"}},
                             project_dir="/proj")
    assert cfg["env"]["X"] == "from-agent"    # agent fix wins (deterministic override)
    assert cfg["project_dir"] == "/proj"
    assert cfg["start_command"] == "npm start"
    assert cfg["disks"] == [{"name": "d", "mountTo": "db"}]


# ---------------------------------------------------------------- factory

def test_factory_selects_by_env(monkeypatch):
    monkeypatch.setenv("DEPLOYER", "liara_api")
    monkeypatch.setenv("LIARA_API_TOKEN", TOKEN)
    assert make_deployer().name == "liara-api"

    monkeypatch.setenv("DEPLOYER", "mock")
    assert make_deployer().name == "mock"

    monkeypatch.setenv("DEPLOYER", "liara_api")
    monkeypatch.setenv("DEMO_MODE", "1")      # demo always wins -> offline
    assert make_deployer().name == "mock"

# ---------------------------------------------------------------- release state machine (VERIFIED live 2026-10-22)

@respx.mock
def test_release_failure_surfaces_real_build_logs(tmp_path):
    """VERIFIED live: GET /v2/releases/{id}/build-logs carries the REAL build
    output — failure lines must land in deploy logs for the self-fix loop."""
    respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(200, json={"project": {"status": "ACTIVE", "isDeployed": True}}))
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-bad"}))
    respx.get(f"{BASE}/v1/projects/my-app/logs").mock(
        return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/v2/releases/rel-bad/build-logs").mock(side_effect=[
        httpx.Response(200, json={"release": {"state": "BUILDING"},
                                  "buildOutput": [{"stream": "STDOUT", "line": "npm ERR! missing",
                                                   "createdAt": "2026-10-22T20:01:00Z"}]}),
        httpx.Response(200, json={"release": {"state": "FAILED", "failReason": "build exited 1"},
                                  "buildOutput": [{"stream": "STDERR",
                                                   "line": "Error: Cannot find module '/app/missing-entry.js'",
                                                   "createdAt": "2026-10-22T20:02:00Z"}]}),
    ])

    d = deployer()
    res = d.deploy({"app_name": "my-app", "platform": "node", "port": 3000,
                    "env": {}, "required_env": [], "prepared_files": [],
                    "disks": [], "project_dir": tmp_project()})
    assert res["ok"] is False and res["kind"] == "build_failed"
    assert "FAILED" in res["message"] and "build exited 1" in res["message"]
    blob = "\n".join(res["logs"])
    assert "Cannot find module" in blob      # real evidence for the diagnose node
    assert "npm ERR! missing" in blob


@respx.mock
def test_release_unhealthy_reports_failure(tmp_path):
    respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(200, json={"project": {"status": "ACTIVE", "isDeployed": True}}))
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-u"}))
    respx.get(f"{BASE}/v1/projects/my-app/logs").mock(
        return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/v2/releases/rel-u/build-logs").mock(
        return_value=httpx.Response(200, json={"release": {"state": "UNHEALTHY"},
                                               "buildOutput": [{"stream": "STDERR",
                                                                "line": "container health check failed"}]}))
    res = deployer().deploy({"app_name": "my-app", "platform": "node", "port": 3000,
                             "env": {}, "required_env": [], "prepared_files": [],
                             "disks": [], "project_dir": tmp_project()})
    # UNHEALTHY routes to unhealthy_url (same evidence family as a dead URL)
    assert res["ok"] is False and res["kind"] == "unhealthy_url"
    assert "UNHEALTHY" in res["message"]


@respx.mock
def test_missing_release_id_is_deterministic_failure(tmp_path):
    respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(200, json={"project": {"status": "ACTIVE", "isDeployed": True}}))
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={}))
    res = deployer().deploy({"app_name": "my-app", "platform": "node", "port": 3000,
                             "env": {}, "required_env": [], "prepared_files": [],
                             "disks": [], "project_dir": tmp_project()})
    assert res["ok"] is False and "releaseID" in res["message"]




@respx.mock
def test_release_unhealthy_routes_to_unhealthy_url_kind(tmp_path):
    """UNHEALTHY = built but not serving: the start-file diagnosis family must
    receive it (same root cause as a failed live-URL check)."""
    respx.get(f"{BASE}/v1/projects/my-app").mock(
        return_value=httpx.Response(200, json={"project": {"status": "ACTIVE", "isDeployed": True}}))
    respx.post(f"{BASE}/v2/projects/my-app/sources").mock(
        return_value=httpx.Response(200, json={"sourceID": "s"}))
    respx.post(f"{BASE}/v2/projects/my-app/releases").mock(
        return_value=httpx.Response(200, json={"releaseID": "rel-uh"}))
    respx.get(f"{BASE}/v1/projects/my-app/logs").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/v2/releases/rel-uh/build-logs").mock(
        return_value=httpx.Response(200, json={"release": {"state": "UNHEALTHY"}, "buildOutput": []}))
    res = deployer().deploy({"app_name": "my-app", "platform": "node", "port": 3000,
                             "env": {}, "required_env": [], "prepared_files": [],
                             "disks": [], "project_dir": tmp_project()})
    assert res["ok"] is False and res["kind"] == "unhealthy_url"
