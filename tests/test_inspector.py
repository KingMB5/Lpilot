"""T2 — inspector: platform/port/commands detection and risk rules R1-R6.

All cases build tiny throw-away projects with tmp_path; repo fixtures are
exercised in the T9 end-to-end suite.
"""

from __future__ import annotations

import json

from inspector.core import inspect

R1 = "R1_sqlite_without_disk"
R2 = "R2_default_secret"
R3 = "R3_vendor_sdk_dependency"
R4 = "R4_lockfile_or_package_manager_mismatch"
R5 = "R5_junk_included"
R6 = "R6_missing_env_vars"


def write(path, content: str):
    p = path.parent
    p.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def risk_ids(result: dict) -> set[str]:
    return {r["id"] for r in result["risks"]}


def risk(result: dict, rid: str) -> dict:
    return next(r for r in result["risks"] if r["id"] == rid)


def test_static_platform_clean(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    r = inspect(str(tmp_path))
    assert r["platform"] == "static"
    assert r["build_command"] is None and r["start_command"] is None
    assert r["risks"] == []


def test_next_project_full_detection(tmp_path):
    write(
        tmp_path / "package.json",
        json.dumps(
            {
                "name": "shop",
                "scripts": {"dev": "next dev", "build": "next build", "start": "next start -p 3000"},
                "dependencies": {"next": "16.0.0", "react": "19.0.0"},
            }
        ),
    )
    write(
        tmp_path / "prisma" / "schema.prisma",
        'datasource db { provider = "sqlite"  url = env("DATABASE_URL") }',
    )
    write(tmp_path / "app" / "page.tsx", "export default function P(){return <div/>}")
    write(tmp_path / "lib" / "db.ts", "const url = process.env.DATABASE_URL;")
    write(tmp_path / "lib" / "auth.ts", 'const pw = process.env.ADMIN_PASSWORD || "benis1404";')
    write(
        tmp_path / ".env.example",
        "DATABASE_URL=file:./dev.db\nADMIN_PASSWORD=benis1404\n",
    )
    (tmp_path / "bun.lock").write_text('{}\n', encoding="utf-8")

    r = inspect(str(tmp_path))
    assert r["platform"] == "next"
    assert r["port"] == 3000
    assert r["build_command"] == "npm run build"
    assert r["start_command"] == "npm run start"
    assert "DATABASE_URL" in r["env_vars_required"]
    assert "ADMIN_PASSWORD" in r["env_vars_required"]
    ids = risk_ids(r)
    assert {R1, R2, R4} <= ids
    assert R1 not in ids or risk(r, R1)["evidence_file"] == "prisma/schema.prisma"
    assert risk(r, R2)["evidence_file"] == ".env.example"
    assert risk(r, R4)["evidence_file"] == "bun.lock"
    # severity ordering: high risks first, deterministic
    sev = [x["severity"] for x in r["risks"]]
    assert sev == sorted(sev, key={"high": 0, "medium": 1, "low": 2}.get)
    # concept keys link risks to the glossary
    assert set(risk(r, R1)["concept_keys"]) >= {"database", "persistent_disk"}


def test_r1_via_local_db_file(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "a", "scripts": {"start": "node s.js"}}))
    write(tmp_path / "s.js", "require('express').().listen(process.env.PORT || 3000)")
    (tmp_path / "data.sqlite").write_bytes(b"SQLite format 3\x00")
    r = inspect(str(tmp_path))
    assert R1 in risk_ids(r)
    assert risk(r, R1)["evidence_file"] == "data.sqlite"


def test_r2_code_fallback_secret(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "b", "scripts": {"start": "node s.js"}}))
    write(tmp_path / "s.js", 'const t = process.env.API_TOKEN || "super-secret-token-123";')
    write(tmp_path / ".env.example", "PORT=3000\n")
    r = inspect(str(tmp_path))
    assert R2 in risk_ids(r)
    assert risk(r, R2)["evidence_file"] == "s.js"


def test_r2_ignores_placeholders(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "c", "scripts": {"start": "node s.js"}}))
    write(tmp_path / "s.js", 'const t = process.env.API_TOKEN || "changeme-please";')
    write(tmp_path / ".env.example", "API_TOKEN=changeme-please\n")
    r = inspect(str(tmp_path))
    assert R2 not in risk_ids(r)


def test_r3_vendor_sdk(tmp_path):
    write(
        tmp_path / "package.json",
        json.dumps(
            {
                "name": "d",
                "scripts": {"start": "node s.js"},
                "dependencies": {"z-ai-web-dev-sdk": "^1.0.0"},
            }
        ),
    )
    write(tmp_path / "s.js", "listen(3000)")
    r = inspect(str(tmp_path))
    assert R3 in risk_ids(r)
    assert risk(r, R3)["evidence_file"] == "package.json"


def test_r4_multiple_lockfiles(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "e", "scripts": {"start": "node s.js"}}))
    write(tmp_path / "s.js", "listen(3000)")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("# yarn\n", encoding="utf-8")
    r = inspect(str(tmp_path))
    assert R4 in risk_ids(r)


def test_r4_npm_lockfile_is_clean(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "f", "scripts": {"start": "node s.js"}}))
    write(tmp_path / "s.js", "listen(3000)")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    r = inspect(str(tmp_path))
    assert R4 not in risk_ids(r)


def test_r5_junk_included(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "g", "scripts": {"start": "node s.js"}}))
    write(tmp_path / "s.js", "listen(3000)")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "index.js").write_text("1", encoding="utf-8")
    (tmp_path / "dev.db").write_bytes(b"x")
    r = inspect(str(tmp_path))
    assert R5 in risk_ids(r)


def test_r6_missing_env_vars_with_and_without_example(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "h", "scripts": {"start": "node s.js"}}))
    write(tmp_path / "s.js", "const k = process.env.OPENAI_API_KEY; listen(3000)")
    r = inspect(str(tmp_path))
    assert R6 in risk_ids(r)
    assert "OPENAI_API_KEY" in risk(r, R6)["detail"]

    write(tmp_path / ".env.example", "PORT=3000\n")
    r2 = inspect(str(tmp_path))
    assert R6 in risk_ids(r2)

    write(tmp_path / ".env.example", "PORT=3000\nOPENAI_API_KEY=\n")
    r3 = inspect(str(tmp_path))
    assert R6 not in risk_ids(r3)


def test_node_express_clean_project(tmp_path):
    write(
        tmp_path / "package.json",
        json.dumps({"name": "srv", "scripts": {"start": "node server.js"}, "dependencies": {"express": "4"}}),
    )
    write(tmp_path / "server.js", "app.listen(process.env.PORT || 3000)")
    write(tmp_path / ".env.example", "PORT=3000\n")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    r = inspect(str(tmp_path))
    assert r["platform"] == "node"
    assert r["port"] == 3000
    assert r["start_command"] == "npm run start"
    assert r["risks"] == []


def test_every_risk_shape(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "i", "scripts": {"start": "node s.js"}}))
    write(tmp_path / "s.js", 'const p = process.env.ADMIN_PASSWORD || "benis1404"; listen(3000)')
    r = inspect(str(tmp_path))
    for risk_obj in r["risks"]:
        assert set(risk_obj) == {"id", "severity", "evidence_file", "concept_keys", "detail"}
        assert risk_obj["severity"] in {"high", "medium", "low"}
        assert isinstance(risk_obj["concept_keys"], list) and risk_obj["concept_keys"]
        assert risk_obj["detail"].strip()
