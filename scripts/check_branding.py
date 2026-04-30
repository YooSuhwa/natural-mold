#!/usr/bin/env python3
"""Branding/license guard for greenfield credentials rewrite.

Verifies the Moldy codebase does NOT include third-party brand identifiers,
package imports, or asset hashes that would violate the licensing & branding
rules established in ADR-009. Run as a CI gate.

Checks:
1. Forbidden identifier `\\bn8n\\b` (case-insensitive) in source trees
2. Forbidden npm scope `@n8n/*` in `package.json`
3. Forbidden asset SHA-256 hashes in `frontend/public/` + `frontend/src/assets/`

Whitelist:
- `NOTICES.md` (the single attribution file is exempt)
- Files under `tasks/archive/` (historical records)
- This script itself

Exit code: 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# -- Configuration -----------------------------------------------------------

FORBIDDEN_IDENTIFIER = re.compile(r"\bn8n\b", re.IGNORECASE)

# Source roots to scan (production code & user-facing docs)
SOURCE_ROOTS = [
    "backend/app",
    "backend/tests",
    "backend/scripts",
    "backend/alembic",
    "frontend/src",
    "frontend/e2e",
    "scripts",
]

# Top-level user-facing docs to scan (governance/ADRs are exempt below)
TOP_LEVEL_DOCS = ["README.md"]

# Product docs to scan (ARCHITECTURE/PRD/etc — not ADRs)
PRODUCT_DOCS = [
    "docs/ARCHITECTURE.md",
    "docs/PRD.md",
    "docs/PRD-screens.md",
    "docs/QUALITY_SCORE.md",
    "docs/tool-setup-guide.md",
]

# Files whose presence is explicitly allowed to mention the forbidden identifier
# (attribution, governance, decision records, and the guard itself)
WHITELIST_PATHS = {
    "NOTICES.md",
    "PLAN.md",
    "CHECKPOINT.md",
    "AUDIT.log",
    "progress.txt",
    "scripts/check_branding.py",
    "backend/tests/test_branding.py",
}

# Directory prefixes whose contents are skipped entirely
# (archives, build outputs, vendored deps, decision records)
SKIP_PREFIXES = (
    "tasks/",
    "node_modules/",
    ".next/",
    ".venv/",
    "dist/",
    "build/",
    "frontend/.next/",
    "backend/.venv/",
    "docs/design-docs/",  # ADRs document third-party borrowings by design
    "docs/exec-plans/",   # execution plans may reference sources
    "docs/references/",   # reference materials
)

# File extensions to scan for the forbidden identifier
SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".toml", ".yaml", ".yml", ".json", ".html", ".css", ".sql"}

# Package manifests to inspect for forbidden scopes
PACKAGE_JSON_FILES = ["frontend/package.json"]
PYPROJECT_FILES = ["backend/pyproject.toml"]
FORBIDDEN_NPM_SCOPE_PREFIX = "@n8n/"
FORBIDDEN_PYPI_PREFIXES = ("n8n",)

# Asset hash blacklist — populate with known third-party logo SHA-256 hashes.
# Empty by default; a future contributor can add hashes after policy review.
ASSET_HASH_BLACKLIST: set[str] = set()
ASSET_DIRS = ["frontend/public", "frontend/src/assets"]
ASSET_EXTENSIONS = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".gif"}


# -- Helpers -----------------------------------------------------------------


def _is_skipped(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in SKIP_PREFIXES)


def _is_whitelisted(rel_path: str) -> bool:
    return rel_path in WHITELIST_PATHS


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            rel = str(path.relative_to(ROOT))
            if _is_skipped(rel) or _is_whitelisted(rel):
                continue
            files.append(path)

    for name in TOP_LEVEL_DOCS + PRODUCT_DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        rel = str(path.relative_to(ROOT))
        if _is_whitelisted(rel):
            continue
        files.append(path)

    return files


def _scan_identifier(violations: list[str]) -> None:
    for path in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_IDENTIFIER.search(line):
                rel = path.relative_to(ROOT)
                violations.append(f"{rel}:{line_no}: {line.strip()[:120]}")


def _scan_npm_packages(violations: list[str]) -> None:
    for rel in PACKAGE_JSON_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            violations.append(f"{rel}: failed to parse ({exc})")
            continue
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            for pkg in (data.get(section) or {}):
                if pkg.startswith(FORBIDDEN_NPM_SCOPE_PREFIX):
                    violations.append(f"{rel}: forbidden npm package '{pkg}' in {section}")


def _scan_pyproject(violations: list[str]) -> None:
    for rel in PYPROJECT_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip().lower()
            if stripped.startswith("#"):
                continue
            for prefix in FORBIDDEN_PYPI_PREFIXES:
                if re.search(rf'["\']({re.escape(prefix)}[a-z0-9_\-]*)', stripped):
                    violations.append(f"{rel}:{line_no}: suspect dependency '{stripped[:80]}'")
                    break


def _scan_assets(violations: list[str]) -> None:
    if not ASSET_HASH_BLACKLIST:
        return
    for rel in ASSET_DIRS:
        base = ROOT / rel
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in ASSET_EXTENSIONS:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in ASSET_HASH_BLACKLIST:
                violations.append(f"{path.relative_to(ROOT)}: blacklisted asset (sha256={digest})")


def main() -> int:
    violations: list[str] = []
    _scan_identifier(violations)
    _scan_npm_packages(violations)
    _scan_pyproject(violations)
    _scan_assets(violations)

    if violations:
        print("Branding check FAILED — the following violations were found:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print(f"\nTotal: {len(violations)} violation(s).", file=sys.stderr)
        print(
            "Refer to NOTICES.md for the policy. The only file allowed to mention\n"
            "the forbidden identifier is NOTICES.md (attribution exception).",
            file=sys.stderr,
        )
        return 1

    print("Branding check PASSED — no forbidden identifiers, packages, or assets found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
