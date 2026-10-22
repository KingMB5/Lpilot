"""Source-artifact builder for POST /v2/projects/{name}/sources.

VERIFIED live 2026-10-22 (official CLI src/utils/create-archive.ts +
src/services/upload.ts, and a real build): the endpoint takes multipart
form-data with one binary field named "file" whose payload is a **gzip-compressed
tar archive** of the project directory — NOT a zip. A zip uploads fine but the
Liara builder fails during extraction ("An error occurred during extraction").
The CLI also ignores liara.json inside the archive (platform config travels in
the release body); we keep ours — the builder tolerates it and it documents the
deployed port/platform.

Security rules applied here (AGENTS.md):
- Secrets never travel inside the artifact: .env and secret-looking files are
  excluded; real values go to the target via POST /v1/projects/update-envs only.
- Junk (node_modules, .next, build caches) is excluded — smaller artifact, no R5.
- Prepared files (liara.json / package.json overrides we generated) are written
  at the archive root and MAY override same-named project files — they are ours,
  generated post-approval.
- No user code is executed here; we only read bytes.
"""

from __future__ import annotations

import fnmatch
import io
import tarfile
from pathlib import Path

from app.config import load_defaults

ARCHIVE_NAME = "sources.tar.gz"

# Kept as an alias for readability at call sites (historically ZIP_NAME).
ZIP_NAME = ARCHIVE_NAME


def _exclude_patterns() -> list[str]:
    return list(load_defaults().get("deploy", {}).get("source_zip_exclude", []))


def _is_junk(rel: str, patterns: list[str]) -> bool:
    """rel is a posix-style relative path; match each path segment and the name."""
    parts = rel.split("/")
    for p in patterns:
        if any(fnmatch.fnmatch(seg, p) or seg == p for seg in parts):
            return True
    return False


def build_source_zip(project_dir: str, prepared_files: list[dict] | None = None,
                     extra_exclude: list[str] | None = None) -> bytes:
    """Build a gzip tar archive of the project directory (bytes in memory).

    The historical name `build_source_zip` is kept: every deployer and test
    calls it. The FORMAT is tar.gz per the VERIFIED live contract above —
    entries relative to the project root, files only, prepared files last so
    they override same-named members. Returns the bytes for the multipart
    "file" field.
    """
    patterns = _exclude_patterns() + list(extra_exclude or [])
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project dir missing: {root}")

    members: list[tuple[str, bytes]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_junk(rel, patterns):
            continue
        members.append((rel, path.read_bytes()))
    for f in prepared_files or []:
        name = str(f.get("name") or "").strip().lstrip("/")
        content = f.get("content")
        if not name or content is None:
            continue
        # prepared files are ours; allow override (e.g. generated liara.json)
        members = [(n, b) for n, b in members if n != name]
        members.append((name, str(content).encode("utf-8")))
    if not members:
        raise ValueError("source archive would be empty — nothing to deploy")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9,
                      format=tarfile.GNU_FORMAT) as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0  # deterministic artifact (no timestamps of the day)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def describe_zip(data: bytes) -> dict:
    """Safe metadata for logs/dry-run output (names only, never file contents)."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]
    return {"filename": ARCHIVE_NAME, "entries": len(names),
            "bytes": len(data), "has_liara_json": "liara.json" in names}
