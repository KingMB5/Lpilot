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
    total = 0
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
            total += 1
            if total > max_files:
                sc.truncated = True
                return sc
            try:
                size = os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
            total += size
            if total > max_bytes:
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
            if ext in TEXT_EXTS and 0 < size <= max_text:
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


def _env_vars_required(sc: Scan) -> list[str]:
    found: set[str] = set()
    for rel, text in sc.texts.items():
        if os.path.splitext(rel)[1] not in CODE_EXTS:
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
        return 3000 if "PORT" in _env_vars_required(sc) else None
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


def _commands(sc: Scan, platform: str) -> tuple[str | None, str | None]:
    pkg = sc.package_json or {}
    scripts = pkg.get("scripts") or {}
    build = "npm run build" if scripts.get("build") else None
    start = "npm run start" if scripts.get("start") else None
    if platform == "next" and not start:
        start = "npm run start"
    if platform == "python":
        build = None
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
    env_required = _env_vars_required(sc)
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
