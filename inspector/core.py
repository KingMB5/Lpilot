"""Pure, deterministic project inspection.

inspect(project_dir) scans an extracted project folder and returns a JSON-able
dict:

    {
      "platform": "static|node|next|python|docker|unknown",
      "port": int | None,
      "build_command": str | None,
      "start_command": str | None,
      "env_vars_required": [...],
      "risks": [ {id, severity, evidence_file, concept_keys, detail}, ... ]
    }

No LLM, no network, no code execution. All rules live in inspector/rules.py.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from app.config import limits
from inspector import rules as rules_mod

CODE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py"}
TEXT_EXTS = CODE_EXTS | {
    ".json", ".md", ".html", ".css", ".prisma", ".yaml", ".yml",
    ".txt", ".env", ".example", ".toml", ".lock", ".log",
}
SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv"}
JUNK_DIRS = {"node_modules", ".next", "dist", "build"}
DB_EXTS = {".db", ".sqlite", ".sqlite3"}

ENV_VAR_RE = re.compile(
    r"""process\.env\.([A-Z_][A-Z0-9_]*)"""
    r"""|process\.env\[[\"']([A-Z_][A-Z0-9_]*)[\"']\]"""
    r"""|os\.environ\.get\(\s*[\"']([A-Z_][A-Z0-9_]*)[\"']"""
    r"""|os\.environ\[\s*[\"']([A-Z_][A-Z0-9_]*)[\"']\s*\]"""
    r"""|import\.meta\.env\.([A-Z_][A-Z0-9_]*)"""
)
LISTEN_PORT_RE = re.compile(
    r"""listen\(\s*(?:process\.env\.PORT|PORT)\s*(?:\|\|\s*(\d{2,5}))?"""
    r"""|listen\(\s*(\d{2,5})\b"""
)
PY_PORT_RE = re.compile(r"""(?:app\.run|uvicorn\.run)\([^)]*?port\s*=\s*(\d{2,5})""")
DOCKER_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+(\d{2,5})\s*$", re.M)


@dataclass
class Scan:
    root: str
    files: list[str] = field(default_factory=list)          # repo-relative paths
    junk_dirs: list[str] = field(default_factory=list)       # junk dirs actually present
    db_files: list[str] = field(default_factory=list)
    package_json: dict | None = None
    package_json_path: str | None = None
    env_example: dict[str, str] = field(default_factory=dict)
    env_example_path: str | None = None
    prisma_path: str | None = None
    prisma_provider: str | None = None
    dockerfile: str | None = None
    # small text contents for rule scanning: rel_path -> content
    texts: dict[str, str] = field(default_factory=dict)
    truncated: bool = False  # True if scan hit file/size limits


def _is_db(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in DB_EXTS


def scan_project(project_dir: str) -> Scan:
    lim = limits()
    max_files = int(lim.get("max_files", 4000))
    max_bytes = int(lim.get("max_total_extract_mb", 120)) * 1024 * 1024
    max_text = 512 * 1024  # per-file cap for text scanning

    sc = Scan(root=project_dir)
    # BUGFIX 2026-10-24: file COUNT and BYTES were previously accumulated into
    # ONE variable, so any project whose (file_count + total_bytes) crossed the
    # file limit (4000) got silently truncated mid-scan — e.g. a 6-file, 6KB
    # project died on file #4 with truncated=True and platform=unknown. Two
    # separate counters now.
    count = 0
    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, project_dir)
        rel_dir = "" if rel_dir == "." else rel_dir
        base = os.path.basename(rel_dir) if rel_dir else ""
        if base in JUNK_DIRS and base not in sc.junk_dirs:
            sc.junk_dirs.append(base)
            dirnames[:] = []  # do not descend into junk
            continue
        for name in sorted(filenames):
            rel = os.path.join(rel_dir, name) if rel_dir else name
            rel = rel.replace(os.sep, "/")
            sc.files.append(rel)
            count += 1
            if count > max_files:
                sc.truncated = True
                return sc
            try:
                size = os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
            total_bytes += size
            if total_bytes > max_bytes:
                sc.truncated = True
                return sc
            ext = os.path.splitext(name)[1].lower()
            if _is_db(name):
                sc.db_files.append(rel)
            if rel == "package.json":
                sc.package_json_path = rel
            elif rel == ".env.example":
                sc.env_example_path = rel
            elif rel == "prisma/schema.prisma":
                sc.prisma_path = rel
            elif rel in ("Dockerfile", "dockerfile"):
                sc.dockerfile = rel
            # extensionless config files are text too (Procfile is the Python
            # start-command convention and feeds the liara.json args plan)
            is_text = ext in TEXT_EXTS or name in ("Procfile", "procfile")
            if is_text and 0 < size <= max_text:
                try:
                    with open(os.path.join(dirpath, name), "r", encoding="utf-8", errors="replace") as f:
                        sc.texts[rel] = f.read()
                except OSError:
                    pass

    if sc.package_json_path and sc.package_json_path in sc.texts:
        import json
        try:
            sc.package_json = json.loads(sc.texts[sc.package_json_path])
        except ValueError:
            sc.package_json = None
    if sc.env_example_path and sc.env_example_path in sc.texts:
        sc.env_example = _parse_env(sc.texts[sc.env_example_path])
    if sc.prisma_path and sc.prisma_path in sc.texts:
        m = re.search(r'provider\s*=\s*["\'](\w+)["\']', sc.texts[sc.prisma_path])
        sc.prisma_provider = m.group(1) if m else None
    return sc


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip("'\"")
    return out


def _platform(sc: Scan) -> str:
    pkg = sc.package_json or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    has_next_cfg = any(f.startswith("next.config.") for f in sc.files)
    if pkg and ("next" in deps or has_next_cfg):
        return "next"
    if pkg:
        return "node"
    if sc.dockerfile:
        return "docker"
    if any(f in ("requirements.txt", "pyproject.toml", "manage.py", "app.py", "main.py") for f in sc.files):
        return "python"
    if "index.html" in sc.files:
        return "static"
    return "unknown"


JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def _env_vars_required(sc: Scan, platform: str = "unknown") -> list[str]:
    """Platform-aware: a python deployment never runs the repo's .ts/.js files,
    so node-style process.env hits inside them are false positives (real case
    2026-10-24: self-deploy asked for ADMIN_PASSWORD from a bundled fixture)."""
    wanted = {"python": {".py"}, "node": JS_EXTS, "next": JS_EXTS}.get(platform)
    found: set[str] = set()
    for rel, text in sc.texts.items():
        ext = os.path.splitext(rel)[1]
        if ext not in CODE_EXTS:
            continue
        if wanted is not None and ext not in wanted:
            continue
        for m in ENV_VAR_RE.finditer(text):
            name = next(g for g in m.groups() if g)
            if name != "NODE_ENV":
                found.add(name)
    return sorted(found)


def _port(sc: Scan, platform: str) -> int | None:
    if platform == "next":
        return 3000  # Next.js documented default
    if platform in ("node",):
        for rel, text in sc.texts.items():
            if os.path.splitext(rel)[1] not in {".js", ".mjs", ".cjs", ".ts"}:
                continue
            m = LISTEN_PORT_RE.search(text)
            if m:
                literal = next((g for g in m.groups() if g), None)
                if literal:
                    return int(literal)
                break  # listen(PORT) with no literal -> decide via env below
        return 3000 if "PORT" in _env_vars_required(sc, platform) else None
    if platform == "python":
        for rel, text in sc.texts.items():
            if rel.endswith(".py"):
                m = PY_PORT_RE.search(text)
                if m:
                    return int(m.group(1))
        return None
    if platform == "docker" and sc.dockerfile and sc.dockerfile in sc.texts:
        m = DOCKER_EXPOSE_RE.search(sc.texts[sc.dockerfile])
        return int(m.group(1)) if m else None
    return None


NODE_ENTRY_FILES = ("server.js", "server.mjs", "index.js", "index.mjs", "app.js", "main.js")


def _commands(sc: Scan, platform: str) -> tuple[str | None, str | None]:
    pkg = sc.package_json or {}
    scripts = pkg.get("scripts") or {}
    build = "npm run build" if scripts.get("build") else None
    start = "npm run start" if scripts.get("start") else None
    if platform == "next":
        # BUGFIX 2026-10-25 (real user deploy): a Next project whose package.json
        # has NO start script got start="npm run start" -> `npm start` fails with
        # "missing script: start" at runtime (UNHEALTHY). And an AI-generated
        # start="next dev" would ship a dev server to production. Deterministic
        # repair: ship `next start`; deployers/base.py materializes it into
        # package.json scripts.start for the node/next buildpack.
        raw = str(scripts.get("start") or "")
        if "dev" in raw or not raw:
            start = "next start"
    if platform == "node" and not start:
        for name in NODE_ENTRY_FILES:
            if name in sc.files:
                start = f"node {name}"
                break
    if platform == "python":
        build = None
        # Liara's python buildpack runs `python3 main.py` by default and IGNORES
        # a Procfile — the real start command must travel inside liara.json
        # "args" (docs.liara.ir/paas/python/how-tos/deploy-app, checked live).
        # Procfile is still the standard convention, so read it first and turn
        # it into the plan's start command; fall back to the usual entry files.
        proc = sc.texts.get("Procfile") or sc.texts.get("procfile") or ""
        for line in proc.splitlines():
            if ":" in line:
                cmd = line.split(":", 1)[1].strip()
                if cmd:
                    start = cmd
                    break
        else:
            start = None
        if not start:
            if "manage.py" in sc.files:
                start = "python manage.py runserver 0.0.0.0:8000"
            elif "app.py" in sc.files:
                start = "python app.py"
            elif "main.py" in sc.files:
                start = "python main.py"
    if platform == "static":
        build, start = None, None
    return build, start


def inspect(project_dir: str) -> dict:
    sc = scan_project(project_dir)
    platform = _platform(sc)
    port = _port(sc, platform)
    build, start = _commands(sc, platform)
    env_required = _env_vars_required(sc, platform)
    risks = rules_mod.evaluate(sc, platform=platform, env_required=env_required)
    return {
        "platform": platform,
        "port": port,
        "build_command": build,
        "start_command": start,
        "env_vars_required": env_required,
        "risks": [r.to_dict() for r in risks],
        "truncated_scan": sc.truncated,
    }
