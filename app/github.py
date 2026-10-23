"""GitHub project intake — REAL repo fetch via the official REST API.

The user pastes a repo URL (public or private). The server downloads the
branch tarball (Authorization: Bearer <GH_PAT> for private repos), extracts it
under the same safety rules as zip intake (path-traversal protection, size and
entry-count caps, no symlinks/hardlinks/devices), and returns a project
directory ready for the inspector.

Secrets: the PAT lives in env (GH_PAT) — it is never logged, never echoed and
never part of any error message. Error strings are user-facing Persian only.
"""

from __future__ import annotations

import os
import re
import tarfile
import tempfile
from pathlib import Path

import httpx

from app.config import limits

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_ROOT = ROOT / "data" / "uploads"

GITHUB_API = "https://api.github.com"


class GitHubError(Exception):
    """Intake failure whose message is safe (and meant) for the UI."""

    def __init__(self, message_fa: str, status: int = 400) -> None:
        super().__init__(message_fa)
        self.message_fa = message_fa
        self.status = status


_REPO_RE = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_STRIP_RE = re.compile(r"^(https?://)?(www\.)?github\.com/", re.IGNORECASE)
_TREE_RE = re.compile(r"^(?P<o>[^/]+)/(?P<r>[^/]+)/tree/(?P<b>.+)$")
_BLOB_RE = re.compile(r"^(?P<o>[^/]+)/(?P<r>[^/]+)/blob/(?P<b>[^/]+)/?$")
_RESERVED = {
    "orgs", "settings", "notifications", "explore", "topics", "features",
    "pricing", "security", "marketplace", "pulls", "issues", "sponsors",
    "collections", "trending", "search", "dashboard", "account", "new",
}


def parse_repo(url: str) -> tuple[str, str, str | None]:
    """(owner, repo, branch|None) از یک آدرس ریپو یا فرم کوتاه owner/repo."""
    raw = (url or "").strip()
    if not raw:
        raise GitHubError("آدرس ریپو را وارد کن.")
    raw = raw.split("?")[0].split("#")[0].strip().rstrip("/")
    raw = _STRIP_RE.sub("", raw)
    branch: str | None = None
    m = _TREE_RE.match(raw) or _BLOB_RE.match(raw)
    if m:
        raw = f"{m.group('o')}/{m.group('r')}"
        branch = m.group("b")
    m = _REPO_RE.match(raw)
    if not m:
        raise GitHubError("آدرس را این‌طوری بفرست: github.com/نام‌کاربری/نام‌ریپو")
    owner, repo = m.group("owner"), m.group("repo")
    if owner.lower() in _RESERVED or not repo:
        raise GitHubError("این آدرس یک ریپوی گیت‌هاب نیست.")
    return owner, repo, (branch or None)


def _headers(token: str) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "LiaraPilot",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _gh_get(client: httpx.Client, path: str, token: str, *,
            stream: bool = False):
    return client.get(f"{GITHUB_API}{path}", headers=_headers(token),
                      follow_redirects=True, timeout=30.0 if not stream else 60.0)


def _download_limit() -> int:
    """سقف حجم tarball — ۲ برابر سقف استخراج کلی، سقف عضو جداگانه هم اعمال می‌شود."""
    return int(limits().get("max_total_extract_mb", 120)) * 2 * 1024 * 1024


def intake(url: str) -> str:
    """دانلود و استخراج امن ریپو؛ مسیر پروژهٔ آمادهٔ بازرسی را برمی‌گرداند."""
    owner, repo, branch = parse_repo(url)
    token = (os.getenv("GH_PAT", "") or "").strip()
    lim = limits()
    max_files = int(lim.get("max_files", 4000))
    max_total = int(lim.get("max_total_extract_mb", 120)) * 1024 * 1024
    max_member = 64 * 1024 * 1024

    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            meta = _gh_get(client, f"/repos/{owner}/{repo}", token)
            if meta.status_code == 404:
                if token:
                    raise GitHubError(
                        "ریپو پیدا نشد یا توکن گیت‌هاب به آن دسترسی ندارد. "
                        "نام ریپو و دسترسی توکن را چک کن.", 404)
                raise GitHubError(
                    "ریپو پیدا نشد. اگر خصوصی است، توکن گیت‌هاب باید تنظیم شده باشد.", 404)
            if meta.status_code == 403:
                rem = meta.headers.get("x-ratelimit-remaining", "")
                if rem == "0":
                    raise GitHubError("محدودیت نرخ گیت‌هاب فعال شده؛ چند دقیقه بعد تلاش کن.", 429)
                raise GitHubError("دسترسی به این ریپو داده نشد (۴۰۳).", 403)
            if meta.status_code != 200:
                raise GitHubError(f"گیت‌هاب پاسخ {meta.status_code} داد؛ دوباره تلاش کن.", 502)
            info = meta.json() or {}
            if isinstance(info.get("size"), (int, float)) and info["size"] * 1024 > _download_limit():
                raise GitHubError("ریپو برای دریافت مستقیم خیلی بزرگ است (سقف ۲۴۰ مگابایت).", 413)
            ref = branch or info.get("default_branch") or "main"

            tarball = _gh_get(client, f"/repos/{owner}/{repo}/tarball/{ref}", token, stream=True)
            if tarball.status_code == 404:
                raise GitHubError(f"برنچ «{ref}» پیدا نشد؛ آدرس یا شاخه را چک کن.", 404)
            if tarball.status_code != 200:
                raise GitHubError(f"دانلود کد ناموفق بود (کد {tarball.status_code}).", 502)

            # Buffer to a temp file first: httpx streaming responses are not
            # file-like for tarfile, and the cap must apply to the raw download.
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
            dl = 0
            try:
                for chunk in tarball.iter_bytes(1024 * 1024):
                    dl += len(chunk)
                    if dl > _download_limit():
                        raise GitHubError("آرشیو ریپو از سقف دریافت (۲۴۰ مگابایت) بزرگ‌تر است.", 413)
                    tmp.write(chunk)
                tmp.close()

                UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
                dest = UPLOAD_ROOT / tempfile.mkdtemp(prefix="gh-", dir=str(UPLOAD_ROOT))
                dest.mkdir(parents=True, exist_ok=True)
                dest_real = os.path.realpath(str(dest))

                files = 0
                total = 0
                with tarfile.open(tmp.name, mode="r:gz") as tf:
                    for member in tf:
                        name = member.name
                        if name in (".", "") or name.startswith(".git/"):
                            continue
                        if member.issym() or member.islnk() or not member.isreg():
                            continue  # symlinks/links/devices never enter the sandbox
                        target = os.path.realpath(os.path.join(dest_real, name))
                        if not (target == dest_real or target.startswith(dest_real + os.sep)):
                            raise GitHubError("مسیر ناامن داخل آرشیو؛ دریافت متوقف شد.", 400)
                        if member.size > max_member:
                            raise GitHubError("فایلی داخل ریپو از سقف حجم بزرگ‌تر است.", 413)
                        total += member.size
                        if total > max_total:
                            raise GitHubError("حجم ریپو از سقف مجاز (۱۲۰ مگابایت) بیشتر است.", 413)
                        if files + 1 > max_files:
                            raise GitHubError("تعداد فایل‌های ریپو از سقف مجاز بیشتر است.", 413)
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        with open(target, "wb") as out:
                            out.write(src.read())
                        files += 1
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            return str(dest)
    except GitHubError:
        raise
    except tarfile.TarError:
        raise GitHubError("محتوای ریپو قابل بازکردن نبود (آرشیو معتبر نبود).", 400) from None
    except httpx.HTTPError:
        raise GitHubError("اتصال به گیت‌هاب برقرار نشد؛ اتصال شبکه را چک کن.", 504) from None
