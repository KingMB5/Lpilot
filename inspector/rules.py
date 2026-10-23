"""Deterministic risk rules R1-R6 over an inspected project (no I/O beyond Scan).

Every rule returns a Risk with a stable id, severity, one evidence file and the
concept keys that link it to /data/concepts.json. Order of results is
deterministic: severity (high>medium>low), then rule id.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from app.config import load_defaults

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

SECRET_KEY_PATTERNS = ["password", "passwd", "secret", "token", "api_key", "apikey"]
PLACEHOLDER_PREFIXES = ["changeme", "change-me", "your-", "example", "xxx", "todo", "insert-", "<", "${", "rep"]
SECRET_MIN_LEN = 6

CODE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py"}

# secret-ish var assigned a literal, e.g. ADMIN_PASSWORD = "..." / adminPassword: "x"
_ASSIGN_SECRET_RE = re.compile(
    r"""([A-Za-z_$][A-Za-z0-9_$-]*(?:password|passwd|secret|token|api_?key)[A-Za-z0-9_$-]*)"""
    r"""\s*[:=]=?\s*["']([^"']{4,})["']""",
    re.IGNORECASE,
)
# a node fallback like process.env + SECRETNAME || literal (kept pattern-free to avoid self-detection)
_FALLBACK_SECRET_RE = re.compile(
    r"""process\.env\.([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY)[A-Z0-9_]*)"""
    r"""\s*\|\|\s*["']([^"']{4,})["']""",
    re.IGNORECASE,
)

LOCKFILES = {
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "bun.lock": "bun",
    "bun.lockb": "bun",
}


@dataclass
class Risk:
    id: str
    severity: str
    evidence_file: str
    concept_keys: list[str]
    detail: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "severity": self.severity,
            "evidence_file": self.evidence_file,
            "concept_keys": self.concept_keys,
            "detail": self.detail,
        }


def _vendor_sdks() -> list[str]:
    return list(load_defaults().get("security", {}).get("vendor_sdk_packages", ["z-ai-web-dev-sdk"]))


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return any(v.startswith(p) for p in PLACEHOLDER_PREFIXES)


def _r1_sqlite_without_disk(sc) -> Risk | None:
    if getattr(sc, "prisma_provider", None) == "sqlite" and sc.prisma_path:
        return Risk(
            id="R1_sqlite_without_disk",
            severity="high",
            evidence_file=sc.prisma_path,
            concept_keys=["database", "persistent_disk", "plan_resources"],
            detail="پروژه از SQLite در Prisma استفاده می‌کند؛ روی لیارا فایل دیتابیس بدون «دیسک ماندگار» با هر استقرار از بین می‌رود.",
        )
    if sc.db_files:
        return Risk(
            id="R1_sqlite_without_disk",
            severity="high",
            evidence_file=sc.db_files[0],
            concept_keys=["database", "persistent_disk", "plan_resources"],
            detail="فایل دیتابیس محلی (SQLite) در پروژه هست؛ روی لیارا برای ماندگاری به دیسک نیاز دارد.",
        )
    return None


def _r2_default_secret(sc) -> Risk | None:
    # 1) .env.example carries a real-looking default secret
    if sc.env_example:
        for key in sorted(sc.env_example):
            v = sc.env_example[key]
            k = key.lower()
            if any(p in k for p in SECRET_KEY_PATTERNS) and not _is_placeholder(v) and len(v) >= SECRET_MIN_LEN:
                return Risk(
                    id="R2_default_secret",
                    severity="high",
                    evidence_file=sc.env_example_path or ".env.example",
                    concept_keys=["secret", "environment_variable"],
                    detail="یک مقدار پیش‌فرض حساس (رمز/توکن) داخل مخزن ثبت شده و عمومی است؛ قبل از استقرار باید عوض شود و از کد حذف گردد.",
                )
    # 2) hardcoded secret / env fallback literal in code
    for rel in sorted(sc.texts):
        if os.path.splitext(rel)[1] not in CODE_EXTS:
            continue
        text = sc.texts[rel]
        for rx in (_FALLBACK_SECRET_RE, _ASSIGN_SECRET_RE):
            for m in rx.finditer(text):
                value = m.group(2)
                if _is_placeholder(value) or len(value) < SECRET_MIN_LEN:
                    continue
                return Risk(
                    id="R2_default_secret",
                    severity="high",
                    evidence_file=rel,
                    concept_keys=["secret", "environment_variable"],
                    detail="یک مقدار رمز/توکن پیش‌فرض به‌صورت literal در کد مانده است؛ باید به متغیر محیطی بدون مقدار پیش‌فرض منتقل شود.",
                )
    return None


def _r3_vendor_sdk(sc) -> Risk | None:
    pkg = sc.package_json or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    for vendor in _vendor_sdks():
        if vendor in deps and sc.package_json_path:
            return Risk(
                id="R3_vendor_sdk_dependency",
                severity="medium",
                evidence_file=sc.package_json_path,
                concept_keys=["platform", "plan_resources"],
                detail=f"وابستگی «{vendor}» ممکن است خارج از محیط سازنده‌اش کار نکند؛ رفتارش روی لیارا باید بررسی شود.",
            )
    return None


def _r4_lockfile_mismatch(sc) -> Risk | None:
    present = sorted({f for f in sc.files if os.path.basename(f) in LOCKFILES and "/" not in f})
    if len(present) > 1:
        return Risk(
            id="R4_lockfile_or_package_manager_mismatch",
            severity="medium",
            evidence_file=present[0],
            concept_keys=["lockfile", "build"],
            detail="چند lockfile مختلف همزمان وجود دارد ("
            + "، ".join(present)
            + ")؛ نسخه‌های نصب‌شده مبهم می‌شود و build ممکن است متفاوت از سیستم خودت باشد.",
        )
    if not present:
        return None
    lock = present[0]
    manager = LOCKFILES[os.path.basename(lock)]
    if manager == "npm":
        return None  # npm lockfile matches the default npm-based build (UNVERIFIED builder)
    pkg = sc.package_json or {}
    pm = str(pkg.get("packageManager", ""))
    declared = pm.split("@")[0].strip() if pm else ""
    detail = (
        f"lockfile مخصوص «{manager}» هست ولی فرآیند build روی لیارا به‌طور پیش‌فرض npm را فرض می‌کند (UNVERIFIED)."
        if declared != manager
        else f"پروژه با «{manager}» مدیریت می‌شود؛ مطمئن شو محیط build لیارا همان package manager را اجرا می‌کند (UNVERIFIED)."
    )
    return Risk(
        id="R4_lockfile_or_package_manager_mismatch",
        severity="medium",
        evidence_file=lock,
        concept_keys=["lockfile", "build"],
        detail=detail,
    )


def _r5_junk_included(sc) -> Risk | None:
    items = list(sc.junk_dirs) + list(sc.db_files)
    if not items:
        return None
    first = items[0]
    return Risk(
        id="R5_junk_included",
        severity="low",
        evidence_file=first,
        concept_keys=["build", "plan_resources"],
        detail="فایل‌های اضافی ("
        + "، ".join(items[:6])
        + ") داخل بسته هستند؛ خودکار از بستهٔ استقرار حذف می‌شوند و نیازی به کاری از طرف تو نیست.",
    )


def _r6_missing_env_vars(sc, env_required: list[str]) -> Risk | None:
    declared = set(sc.env_example.keys())
    missing = [v for v in env_required if v not in declared]
    if not missing:
        return None
    if sc.env_example_path:
        detail = "این متغیرهای محیطی در کد استفاده شده‌اند ولی در .env.example تعریف نشده‌اند: " + "، ".join(missing)
    else:
        detail = "فایل .env.example وجود ندارد و این متغیرها در کد لازم‌اند: " + "، ".join(missing)
    return Risk(
        id="R6_missing_env_vars",
        severity="high",
        evidence_file=sc.env_example_path or ".env.example",
        concept_keys=["environment_variable", "secret"],
        detail=detail,
    )


def evaluate(sc, platform: str, env_required: list[str]) -> list[Risk]:
    rules = [
        _r1_sqlite_without_disk(sc),
        _r2_default_secret(sc),
        _r3_vendor_sdk(sc),
        _r4_lockfile_mismatch(sc),
        _r5_junk_included(sc),
        _r6_missing_env_vars(sc, env_required),
    ]
    found = [r for r in rules if r is not None]
    found.sort(key=lambda r: (SEVERITY_ORDER[r.severity], r.id))
    return found
