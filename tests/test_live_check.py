"""Phase-3 T7 — live_check.py offline unit tests (NO network: only the planning
and gating logic is exercised; live evidence belongs to docs/VERIFIED_MATRIX.md).
"""

from __future__ import annotations

import httpx

import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import live_check as lc  # noqa: E402


def test_tier2_steps_are_never_planned_as_sent_in_dry_run(monkeypatch):
    monkeypatch.setenv("LIARA_PLAN_ID", "")
    st = lc.step_create_app(None, "tok", "probe-app", live=False)
    assert st.status == "SKIPPED" and "not sent" in st.detail
    st2 = lc.step_upload(None, "tok", "probe-app", live=False)
    assert st2.status == "SKIPPED"
    # restart stays planned-out even in live mode without explicit approval
    st3 = lc.step_restart(None, "tok", "probe-app", live=True, approved=False)
    assert st3.status == "SKIPPED" and "approve-restart" in st3.detail


def test_restart_never_sends_without_explicit_approval(monkeypatch):
    sent = []
    monkeypatch.setattr(lc, "req", lambda *a, **k: sent.append(a) or httpx.Response(200))
    st = lc.step_restart(None, "tok", "probe-app", live=True, approved=False)
    assert sent == []  # the request function was never reached
    assert st.status == "SKIPPED"


def test_create_app_never_invents_planid(monkeypatch):
    monkeypatch.setenv("LIARA_PLAN_ID", "")
    calls = []
    monkeypatch.setattr(lc, "req", lambda c, st, m, p, **k: calls.append(k) or httpx.Response(400))
    st = lc.step_create_app(None, "tok", "probe-app", live=True)
    assert st.status == "UNVERIFIED"  # refused BEFORE any invented value
    assert calls == []                # no request without LIARA_PLAN_ID


def test_write_matrix_appends_dated_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "MATRIX_DOC", tmp_path / "VERIFIED_MATRIX.md")
    steps = [lc.Step("auth (GET /v1/projects)", 1), lc.Step("restart", 2)]
    steps[0].status, steps[0].detail = "PASS", "token valid"
    steps[1].status, steps[1].detail = "SKIPPED", "tier-2 planned only"
    lc.write_matrix(steps, "DRY-RUN", "probe-app")
    text = (tmp_path / "VERIFIED_MATRIX.md").read_text(encoding="utf-8")
    assert "## live_check run" in text and "| 1 |" in text and "PASS" in text
    assert "delete" in text.lower()  # tier-3 disclaimer present
    lc.write_matrix(steps, "LIVE", "probe-app")  # second run appends, never overwrites
    text2 = (tmp_path / "VERIFIED_MATRIX.md").read_text(encoding="utf-8")
    assert text2.count("## live_check run") == 2


def test_probe_archive_is_clean():
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(lc.probe_archive()), mode="r:gz") as tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]
    assert names == ["liara.json", "index.html"]
    payload = ""
    with tarfile.open(fileobj=io.BytesIO(lc.probe_archive()), mode="r:gz") as tf:
        for m in tf.getmembers():
            if m.isfile():
                payload += tf.extractfile(m).read().decode()
    assert "secret" not in payload.lower()
