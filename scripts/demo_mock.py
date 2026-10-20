#!/usr/bin/env python3
"""Offline demo: full mock flow on the perfume-store fixture for BOTH levels.

Runs FakeLLM + MockDeployer (no network, no keys). Prints the differing
beginner/expert explanations per risk and the final reports. Exit 0 = success.

Usage: .venv/bin/python scripts/demo_mock.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.graph import build_graph, resume  # noqa: E402
from agent.nodes import Deps  # noqa: E402
from app.redact import SecretRegister, redact_obj  # noqa: E402
from deployers.mock import MockDeployer  # noqa: E402
from llm.client import FakeLLM, RunBudget  # noqa: E402

PROJECT = str(ROOT / "fixtures" / "next-prisma-sqlite")
ENV = {"DATABASE_URL": "file:./db/custom.db", "ADMIN_PASSWORD": "changed-by-user-1",
       "OPENAI_API_KEY": "demo-key-000"}


def concepts() -> dict:
    data = json.loads((ROOT / "data" / "concepts.json").read_text(encoding="utf-8"))
    return {c["key"]: c for c in data["concepts"]}


def run_level(level_kwargs: dict, label: str) -> dict:
    register = SecretRegister()
    events: list[dict] = []
    deps = Deps(
        llm=FakeLLM(register, RunBudget(60000), concepts()),
        deployer=MockDeployer(),
        emit=lambda t, p: events.append({"type": t, "payload": redact_obj(p, register)}),
        concepts=concepts(),
    )
    graph = build_graph(deps)
    cfg = {"configurable": {"thread_id": f"demo-{label}"}}
    out = graph.invoke({"project_dir": PROJECT, **level_kwargs}, cfg)
    assert "__interrupt__" in out, "expected approval interrupt"
    out = resume(graph, cfg, {"approved": True, "cost_ok": True, "env_values": ENV})
    return out


def main() -> int:
    print("=" * 72)
    print("LiaraPilot offline demo — fixture: next-prisma-sqlite (perfume store)")
    print("=" * 72)

    beginner = run_level({"answers": {"q1": "never", "q2": "used", "q3": "know", "q4": "know"}}, "beginner")
    expert = run_level({"skip_expert": True}, "expert")

    ids = [r["id"] for r in beginner["inspection"]["risks"]]
    print(f"\ndetected risks: {', '.join(ids)}")
    assert {"R1_sqlite_without_disk", "R2_default_secret",
            "R3_vendor_sdk_dependency", "R4_lockfile_or_package_manager_mismatch"} <= set(ids), \
        "acceptance failed: R1-R4 not all detected"

    print("\n" + "-" * 72)
    print("SAME RISK, TWO VOICES — R1_sqlite_without_disk")
    print("-" * 72)
    risk = next(r for r in beginner["inspection"]["risks"] if r["id"] == "R1_sqlite_without_disk")
    exp_risk = {"id": risk["id"], "detail": risk["detail"],
                "concept_keys": risk["concept_keys"], "severity": risk["severity"]}
    from llm.explain import fake_explain

    print("\n--- BEGINNER ---\n" + fake_explain(exp_risk, "beginner", concepts()))
    print("\n--- EXPERT ---\n" + fake_explain(exp_risk, "expert", concepts()))

    for label, out in (("BEGINNER", beginner), ("EXPERT", expert)):
        rep = out["report"]
        print("\n" + "-" * 72)
        print(f"{label} REPORT — status={rep['status']} url={rep['url']}")
        print("-" * 72)
        print(f"platform={rep['platform']} port={rep['port']} cost={rep['estimated_cost']}")
        if rep.get("learned"):
            print("چه یاد گرفتی:")
            for item in rep["learned"]:
                print("  ☑", item.split(".")[0] + ".")
        print("decisions:", len(rep["decisions"]), "| fixes:", len(rep["fix_history"]))

    assert beginner["status"] == "done" and expert["status"] == "done"
    assert beginner["report"]["learned"] and not expert["report"].get("learned")
    assert "benis1404" not in json.dumps(beginner["report"]) + json.dumps(expert["report"])
    print("\nDEMO OK ✅  (both levels deployed; secrets absent from reports)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
