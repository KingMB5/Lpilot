"""Safe zip extraction (T10): zip-slip protection, size/count limits, no symlinks,
no code execution — we only unpack bytes and later READ them."""

from __future__ import annotations

import os
import zipfile

from app.config import limits


class ZipSafetyError(Exception):
    pass


def _limit(key: str, default: int) -> int:
    return int(limits().get(key, default))


def extract_zip(zp_path: str, dest_dir: str) -> dict:
    """Extract safely. Returns {files, total_bytes}. Raises ZipSafetyError on abuse."""
    max_files = _limit("max_files", 4000)
    max_total = _limit("max_total_extract_mb", 120) * 1024 * 1024
    max_member = 64 * 1024 * 1024

    dest = os.path.realpath(dest_dir)
    os.makedirs(dest, exist_ok=True)

    files = 0
    total = 0
    with zipfile.ZipFile(zp_path) as zf:
        return _extract_from(zf, infos=zf.infolist(), dest=dest,
                             max_files=max_files, max_total=max_total, max_member=max_member)


def extract_zip_bytes(payload: bytes, dest_dir: str) -> dict:
    """Same as extract_zip for in-memory zips (tests, direct callers)."""
    import io

    max_files = _limit("max_files", 4000)
    max_total = _limit("max_total_extract_mb", 120) * 1024 * 1024
    max_member = 64 * 1024 * 1024
    dest = os.path.realpath(dest_dir)
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return _extract_from(zf, infos=zf.infolist(), dest=dest,
                             max_files=max_files, max_total=max_total, max_member=max_member)


def _extract_from(zf: zipfile.ZipFile, *, infos, dest: str,
                  max_files: int, max_total: int, max_member: int) -> dict:
    files = 0
    total = 0
    for info in infos:
        name = info.filename
        if info.is_dir():
            continue
        # zip-slip: resolved target must stay inside dest
        target = os.path.realpath(os.path.join(dest, name))
        if not (target == dest or target.startswith(dest + os.sep)):
            raise ZipSafetyError(f"unsafe path in zip: {name!r}")
        if info.file_size > max_member:
            raise ZipSafetyError(f"entry too large: {name!r}")
        total += info.file_size
        if total > max_total:
            raise ZipSafetyError("uncompressed size exceeds limit")
        if files + 1 > max_files:
            raise ZipSafetyError(f"zip has too many entries (> {max_files})")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            out.write(src.read())
        files += 1
    return {"files": files, "total_bytes": total}


def project_root_of(dest_dir: str) -> str:
    """If the zip had a single top-level folder, descend into it."""
    entries = [e for e in os.listdir(dest_dir) if e not in ("__MACOSX",)]
    if len(entries) == 1 and os.path.isdir(os.path.join(dest_dir, entries[0])):
        return os.path.join(dest_dir, entries[0])
    return dest_dir
