"""T10 — security: zip-slip, size/count limits, redaction-before-send, no execution."""

from __future__ import annotations

import io
import zipfile

import pytest

from app import zipsafe
from app.redact import SecretRegister
from inspector.core import inspect
from llm.client import _Base, RunBudget


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_slip_rejected(tmp_path):
    malicious = _zip_bytes({"../../evil.txt": b"x"})
    dest = tmp_path / "out"
    with pytest.raises(zipsafe.ZipSafetyError):
        zipsafe.extract_zip_bytes(malicious, str(dest))
    assert not (tmp_path.parent / "evil.txt").exists()


def test_absolute_path_rejected(tmp_path):
    malicious = _zip_bytes({"/etc/evil.txt": b"x"})
    with pytest.raises(zipsafe.ZipSafetyError):
        zipsafe.extract_zip_bytes(malicious, str(tmp_path / "out"))


def test_file_count_limit(tmp_path, monkeypatch):
    many = {f"f{i}.txt": b"x" for i in range(30)}
    payload = _zip_bytes(many)
    monkeypatch.setattr(zipsafe, "_limit", lambda key, default: 10 if key == "max_files" else default)
    with pytest.raises(zipsafe.ZipSafetyError):
        zipsafe.extract_zip_bytes(payload, str(tmp_path / "out"))


def test_total_size_limit(tmp_path, monkeypatch):
    payload = _zip_bytes({"big.bin": b"x" * (2 * 1024 * 1024)})
    monkeypatch.setattr(zipsafe, "_limit", lambda key, default: 1 if key == "max_total_extract_mb" else default)
    with pytest.raises(zipsafe.ZipSafetyError):
        zipsafe.extract_zip_bytes(payload, str(tmp_path / "out"))


def test_normal_zip_extracts(tmp_path):
    payload = _zip_bytes({"package.json": b"{}", "app/page.tsx": b"export default 1;"})
    res = zipsafe.extract_zip_bytes(payload, str(tmp_path / "out"))
    assert res["files"] == 2
    assert (tmp_path / "out" / "package.json").exists()


def test_prompts_are_redacted_before_sending():
    reg = SecretRegister()
    reg.add("benis1404")
    base = _Base(reg, RunBudget(100000))
    s, u = base._prepare("system prompt", "user says ADMIN_PASSWORD=benis1404 please")
    assert "benis1404" not in s + u
    assert "***REDACTED***" in u


def test_inspection_never_executes_uploaded_code(tmp_path):
    # a hostile "install script" that would create a marker file if executed
    script = tmp_path / "install.sh"
    script.write_text("#!/bin/sh\ntouch executed-marker\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"x","scripts":{"start":"node s.js"}}', encoding="utf-8")
    (tmp_path / "s.js").write_text("listen(3000)", encoding="utf-8")
    inspect(str(tmp_path))
    assert not (tmp_path / "executed-marker").exists()
