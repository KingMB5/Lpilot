"""Source-artifact builder for POST /v2/projects/{name}/sources.

Spec (VERIFIED, config/defaults.yaml fact paas_api_endpoints): the endpoint takes
multipart/form-data with one required binary field named "file". The CLI uploads
a zip of the project directory, so we do the same.

Security rules applied here (AGENTS.md):
- Secrets never travel inside the artifact: .env and secret-looking files are
  excluded; real values go to the target via POST /v1/projects/update-envs only.
- Junk (node_modules, .next, build caches) is excluded — smaller artifact, no R5.
- Prepared files (liara.json we generated) are written at the zip root and MAY
  override same-named files in the project — they are ours, generated post-approval.
- No user code is executed here; we only read bytes.
"""

from __future__ import annotations

import fnmatch
import io
import zipfile
from pathlib import Path

from app.config import load_defaults

ZIP_NAME = "sources.zip"


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
    """Zip the project directory (bytes in memory) with exclusions + prepared files.

    prepared_files: [{name, content}] written at the zip root (override allowed).
    Returns the zip bytes ready for the multipart "file" field.
    """
    patterns = _exclude_patterns() + list(extra_exclude or [])
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project dir missing: {root}")

    buf = io.BytesIO()
    written: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if _is_junk(rel, patterns):
                continue
            zf.writestr(rel, path.read_bytes())
            written.add(rel)
        for f in prepared_files or []:
            name = str(f.get("name") or "").strip().lstrip("/")
            content = f.get("content")
            if not name or content is None:
                continue
            # prepared files are ours; allow override (e.g. generated liara.json)
            zf.writestr(name, str(content).encode("utf-8"))
            written.add(name)
    if not written:
        raise ValueError("source zip would be empty — nothing to deploy")
    return buf.getvalue()


def describe_zip(data: bytes) -> dict:
    """Safe metadata for logs/dry-run output (names only, never file contents)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    return {"filename": ZIP_NAME, "entries": len(names),
            "bytes": len(data), "has_liara_json": "liara.json" in names}
