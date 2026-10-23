"""Deployer interface — all real deployment hides behind this (AGENTS.md).

Implementations: MockDeployer (offline/tests/demo), LiaraApiDeployer (Thursday),
LiaraCliDeployer (fallback). No implementation may print or log the user's token.
"""

from __future__ import annotations

import json
import os
import pathlib
import uuid
from typing import Any, Protocol


class Deployer(Protocol):
    name: str

    def deploy(self, config: dict[str, Any]) -> dict:
        """config: {app_name, platform, port, env, required_env, prepared_files,
        project_dir?, start_command?, disks?}
        returns {ok, kind, message, url?, logs: [str], problems?} — kind in
        {bad_port, missing_env_var, wrong_start_script, missing_disk_sqlite,
        build_failed, missing_plan, auth_failed, rate_limited, timeout,
        unknown, success, dry_run, dry_run_failed}"""
        ...

    def logs(self) -> list[str]:
        """Runtime/application logs for the self-fix loop."""
        ...

    def status(self) -> dict:
        """{healthy, url?} health view used by the verify step."""
        ...


def make_deploy_config(inspection: dict, plan: dict, approvals: dict,
                       project_dir: str | None = None) -> dict:
    """Pure helper assembling the deploy payload; env VALUES never go into events.
    Env merge priority: agent fixes (plan.env_fixes) > user approval values > placeholders."""
    env = dict(plan.get("env_placeholders") or {})
    env.update(approvals.get("env_values") or {})
    env.update(plan.get("env_fixes") or {})
    start_command = plan.get("start_command") or inspection.get("start_command")
    # LIVE FIX 2026-10-24 (E2E on lp-dcded0): BYO-token sessions have NO operator
    # LIARA_PLAN_ID env — new-app creation then failed with missing_plan after
    # the user had already approved. Resolve: plan's own field > user's approval
    # choice > env > VERIFIED default plan (small-g2, live 2026-10-22/24).
    plan_id = (str(plan.get("planID") or approvals.get("plan_id")
                   or os.getenv("LIARA_PLAN_ID") or "small-g2").strip())
    cfg = {
        # Unique per deployment: concurrent users must never overwrite each
        # other's app on the shared Liara account (each deploy = own app+URL).
        "app_name": plan.get("app_name") or ("lp-" + uuid.uuid4().hex[:6]),
        "platform": plan.get("platform") or inspection.get("platform"),
        "planID": plan_id,
        "port": plan.get("port"),
        "env": env,
        "required_env": list(inspection.get("env_vars_required") or []),
        "prepared_files": list(plan.get("prepared_files") or []),
        "drop_files": list(plan.get("drop_files") or []),
        "disks": list(plan.get("disks") or []),
        "start_command": start_command,
        "project_dir": project_dir,
    }
    # VERIFIED live 2026-10-22: the Liara node platform runs `npm start`, so a
    # plan start_command only takes effect when package.json carries it. The
    # deployer materializes it as a package.json override in the sources zip
    # (prepared_files entries override same-named files) — deterministic, no
    # code execution, and the original dependencies are preserved.
    # BUGFIX 2026-10-25: this previously ran for platform "node" ONLY, so a
    # Next.js plan's start_command ("next start") never materialized and the
    # container 502'd if package.json lacked a start script.
    try:
        if (str(cfg["platform"]).lower() in ("node", "next") and start_command and project_dir):
            # Start from an agent-prepared override when the fix loop already
            # patched package.json (e.g. patch_dependency CVE bump) — agent
            # fixes must survive into the artifact; else read the project's own.
            pkg = None
            for f in cfg["prepared_files"]:
                if str(f.get("name")) == "package.json":
                    try:
                        pkg = json.loads(str(f.get("content") or "{}"))
                    except ValueError:
                        pkg = None
                    break
            if not isinstance(pkg, dict):
                pkg_file = pathlib.Path(project_dir) / "package.json"
                pkg = json.loads(pkg_file.read_text(encoding="utf-8")) if pkg_file.exists() else {}
            scripts = pkg.setdefault("scripts", {})
            changed = False
            if str(scripts.get("start", "")) != str(start_command):
                scripts["start"] = str(start_command)
                changed = True
            # REAL-WORLD FIX 2026-10-25, REVISED live 2026-10-24 (round-5 E2E):
            # a Prisma project must run `prisma generate` before `next build`.
            # The first attempt added a postinstall hook — but Liara's
            # next-platform builder runs npm install in a two-stage flow and
            # postinstall fired BEFORE the full sources were in place:
            # "Error: Could not find Prisma Schema" killed the build. The build
            # script runs with the complete tree, so ensure it there instead.
            deps_ = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
            has_schema = (pathlib.Path(project_dir) / "prisma" / "schema.prisma").exists()
            build_script = str(scripts.get("build", "")).strip()
            if has_schema and "prisma" in deps_ and build_script and "prisma generate" not in build_script:
                scripts["build"] = "prisma generate && " + build_script
                changed = True
            if changed:
                override = {"name": "package.json",
                            "content": json.dumps(pkg, ensure_ascii=False, indent=2)}
                cfg["prepared_files"] = [f for f in cfg["prepared_files"]
                                         if str(f.get("name")) != "package.json"]
                cfg["prepared_files"].append(override)
    except (OSError, ValueError):
        pass  # unreadable/unparsable package.json — deploy the plan as-is
    return cfg
