"""T3 — repo fixtures: lock the acceptance criteria deterministically."""

from __future__ import annotations

import pathlib

from inspector.core import inspect

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def test_next_prisma_sqlite_yields_R1_R2_R3_R4():
    r = inspect(str(FIXTURES / "next-prisma-sqlite"))
    ids = {x["id"] for x in r["risks"]}
    assert {
        "R1_sqlite_without_disk",
        "R2_default_secret",
        "R3_vendor_sdk_dependency",
        "R4_lockfile_or_package_manager_mismatch",
    } <= ids
    assert r["platform"] == "next"
    assert r["port"] == 3000


def test_broken_project_flags_missing_env():
    r = inspect(str(FIXTURES / "broken-project"))
    r6 = next(x for x in r["risks"] if x["id"] == "R6_missing_env_vars")
    assert "MISSING_TOKEN" in r6["detail"]


def test_plain_html_and_node_express_are_clean():
    assert inspect(str(FIXTURES / "plain-html"))["platform"] == "static"
    node = inspect(str(FIXTURES / "node-express"))
    assert node["platform"] == "node" and node["risks"] == []


def test_fallback_app_has_liara_json_disk():
    lj = (pathlib.Path(__file__).resolve().parent.parent / "samples" / "fallback-app" / "liara.json")
    import json

    cfg = json.loads(lj.read_text(encoding="utf-8"))
    assert cfg["platform"] == "node"
    assert cfg["disks"][0]["mountTo"] == "db"
