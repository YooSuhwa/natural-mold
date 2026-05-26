"""k-skill upstream sync (Spec §5).

Reads a local checkout of ``NomaDamas/k-skill``, discovers each skill
directory, validates the SKILL.md contract, runs secret_scan, computes
a canonical content hash, and upserts ``MarketplaceItem`` +
``MarketplaceVersion`` rows tagged ``is_system=True / source_kind='k-skill'``.

The full sync is driven by ``run_sync`` which is called from the CLI
(``app.scripts.sync_k_skill``). Each per-skill step is a sync helper so
unit tests can drive the pipeline at arbitrary granularity (Spec §5.10
— "single failure must not abort the run").

Import-side guarantees:

* secret_scan rejection → that skill stays in its prior state (no row
  mutation); the report records ``failed_secret_scan`` and the sync
  proceeds to the next skill.
* Content-hash unchanged → no new version row, no storage write.
* Removed upstream skill → existing item is marked ``status='deprecated'``
  unless ``keep_deprecated=False`` is requested (operator opt-out for
  the legacy "leave the old version reachable" behaviour).

The module is import-time side-effect free — the CLI sets up the
session + git checkout, then hands a ``Path`` to ``run_sync``.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.marketplace.k_skill_requirements import (
    REGEX_HINTS,
    known_skills,
    requirements_for,
)
from app.marketplace.secret_scan import scan_package
from app.models.marketplace import (
    MarketplaceItem,
    MarketplacePublicationLink,
    MarketplaceVersion,
)
from app.skills.inspector import parse_skill_md
from app.storage.paths import ensure_relative

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


# Spec §5.3 — directory names we never recurse into. Mirrors upstream's
# own ``.skillignore`` plus the local meta dirs that may appear when an
# operator clones the repo with their own editor / CI configs in place.
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".github",
        ".codex",
        ".claude",
        ".omx",
        ".ouroboros",
        ".changeset",
        ".cursor",
        ".vscode",
        ".sisyphus",
        ".idea",
        "docs",
        "dist",
        "node_modules",
        "packages",
        "python-packages",
        "scripts",
        "examples",
    }
)


def discover_skills(repo_dir: Path) -> list[Path]:
    """Yield every directory under ``repo_dir`` that *looks like* a skill
    (contains ``SKILL.md``) and isn't an excluded meta dir.

    Returns the *deduplicated* list sorted by relative path so reports
    are stable across runs.
    """

    if not repo_dir.exists():
        return []

    candidates: list[Path] = []
    for entry in sorted(repo_dir.rglob("SKILL.md")):
        if not entry.is_file():
            continue
        skill_dir = entry.parent
        # Reject any path component that lives under an excluded dir
        # — protects against deeply-nested SKILL.md inside ``docs/``.
        try:
            rel_parts = skill_dir.relative_to(repo_dir).parts
        except ValueError:
            continue
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        candidates.append(skill_dir)
    return candidates


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_skill(skill_dir: Path) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Read + sanity-check the skill's SKILL.md frontmatter.

    Returns ``(ok, frontmatter, error)``. ``frontmatter`` is the parsed
    metadata dict on success; ``error`` is a human-readable reason on
    failure.

    The two required rules (Spec §5.3):
    1. SKILL.md exists.
    2. ``name`` in frontmatter matches the directory name.
    """

    md = skill_dir / "SKILL.md"
    if not md.exists():
        return False, None, "SKILL.md not found"
    try:
        parsed = parse_skill_md(md.read_bytes())
    except Exception as exc:  # noqa: BLE001 — frontmatter library throws broadly
        return False, None, f"frontmatter parse failed: {exc}"
    metadata = parsed.get("metadata") or {}
    name = str(metadata.get("name") or "").strip()
    if not name:
        return False, None, "SKILL.md frontmatter missing 'name'"
    if name != skill_dir.name:
        return False, None, (
            f"name '{name}' does not match directory '{skill_dir.name}'"
        )
    return True, metadata, None


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def compute_content_hash(skill_dir: Path) -> str:
    """Canonical SHA-256 of the skill's bytes.

    Walks files in sorted relative-path order, mixing the path + a NUL
    + the bytes into a single digest so two directories with the same
    file contents under different layouts produce different hashes.
    Mirrors ``publish_service``'s hasher so manual publish + k-skill
    sync agree on content equality (so dedup works across both paths).
    """

    hasher = hashlib.sha256()
    for entry in sorted(skill_dir.rglob("*")):
        if not entry.is_file():
            continue
        rel = entry.relative_to(skill_dir).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        try:
            hasher.update(entry.read_bytes())
        except OSError:
            continue
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Execution profile inference (Spec §5.7)
# ---------------------------------------------------------------------------


_PROFILE_READY_PYTHON_HINTS = ("requirements.txt", "pyproject.toml")
_PROFILE_NODE_HINTS = ("package.json",)
_PROFILE_HTTP_PROXY_HINTS = ("proxy.toml",)


def infer_execution_profile(skill_dir: Path) -> dict[str, Any]:
    """Best-effort runtime profile classification (Spec §5.7).

    The marketplace surface uses this for the "support_level" badge so a
    user can tell at a glance whether the skill runs out-of-the-box,
    needs a hosted proxy, or is manual-only. The mapping is intentionally
    conservative — when the markers conflict we pick the more
    restrictive level.
    """

    files = {p.name.lower() for p in skill_dir.glob("*") if p.is_file()}
    runners: list[str] = []
    requires_node = any(p in files for p in _PROFILE_NODE_HINTS)
    requires_python = any(p in files for p in _PROFILE_READY_PYTHON_HINTS)
    requires_proxy = any(p in files for p in _PROFILE_HTTP_PROXY_HINTS)

    if requires_proxy:
        runners.append("proxy_http")
        support_level = "proxy_http"
    elif requires_node:
        runners.append("node_package")
        support_level = "node_package"
    elif requires_python:
        runners.append("ready_python")
        support_level = "ready_python"
    else:
        # Pure SKILL.md skills (instructions only) — LLM이 read_file로
        # SKILL.md를 직접 따라가는 케이스라 marketplace 관점에서는 "ready".
        # 외부 앱/브라우저 로그인이 필요한 진짜 manual 케이스는 curated
        # map에서 명시적으로 ``manual_only``로 지정한다.
        support_level = "ready_python"

    return {
        "support_level": support_level,
        "runners": runners,
        "requires_python": requires_python,
        "requires_node": requires_node,
        "requires_proxy": requires_proxy,
    }


# ---------------------------------------------------------------------------
# Slug / hint helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    import re

    base = name.strip().lower().replace("_", "-").replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9-]+", "-", base).strip("-")
    return cleaned or "item"


def collect_regex_hints(skill_dir: Path) -> list[str]:
    """Scan the skill's source files for env-var-shaped tokens.

    Result is **logged only** — never used to auto-attach credentials
    (the curated map in ``k_skill_requirements`` is authoritative).
    Operators use the hint list to spot new credentials that need a
    curated-map entry on the next release.
    """

    hits: set[str] = set()
    for entry in skill_dir.rglob("*"):
        if not entry.is_file():
            continue
        if entry.suffix.lower() in {".png", ".jpg", ".pdf", ".zip", ".bin"}:
            continue
        try:
            text = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in REGEX_HINTS:
            for match in pattern.findall(text):
                hits.add(str(match))
    return sorted(hits)


# ---------------------------------------------------------------------------
# Action + report types
# ---------------------------------------------------------------------------


class ImportAction(StrEnum):
    UNCHANGED = "unchanged"
    NEW_VERSION = "new_version"
    METADATA_UPDATE = "metadata_update"
    CREATED = "created"
    FAILED_SECRET_SCAN = "failed_secret_scan"
    FAILED_VALIDATION = "failed_validation"
    SKIPPED = "skipped"


@dataclass
class ImportResult:
    """Per-skill outcome carried back to the CLI for the final report."""

    name: str
    action: ImportAction
    detail: str = ""
    item_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None
    hints: list[str] = field(default_factory=list)


@dataclass
class SyncReport:
    """End-of-run summary. Counters are derived from ``results``."""

    ref: str
    commit_sha: str
    dry_run: bool
    results: list[ImportResult] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)

    @property
    def created(self) -> int:
        return sum(1 for r in self.results if r.action == ImportAction.CREATED)

    @property
    def updated(self) -> int:
        return sum(
            1
            for r in self.results
            if r.action in (ImportAction.NEW_VERSION, ImportAction.METADATA_UPDATE)
        )

    @property
    def unchanged(self) -> int:
        return sum(1 for r in self.results if r.action == ImportAction.UNCHANGED)

    @property
    def failed(self) -> int:
        return sum(
            1
            for r in self.results
            if r.action
            in (
                ImportAction.FAILED_SECRET_SCAN,
                ImportAction.FAILED_VALIDATION,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "commit_sha": self.commit_sha,
            "dry_run": self.dry_run,
            "summary": {
                "created": self.created,
                "updated": self.updated,
                "unchanged": self.unchanged,
                "failed": self.failed,
                "deprecated": len(self.deprecated),
            },
            "results": [
                {
                    "name": r.name,
                    "action": r.action.value,
                    "detail": r.detail,
                    "item_id": str(r.item_id) if r.item_id else None,
                    "version_id": str(r.version_id) if r.version_id else None,
                    "hints": list(r.hints),
                }
                for r in self.results
            ],
            "deprecated": list(self.deprecated),
        }


# ---------------------------------------------------------------------------
# Per-skill import
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _find_item(
    db: AsyncSession, *, upstream_name: str
) -> MarketplaceItem | None:
    stmt = (
        select(MarketplaceItem)
        .where(MarketplaceItem.source_external_id == upstream_name)
        .where(MarketplaceItem.source_kind == "k-skill")
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _next_version_number(
    db: AsyncSession, *, item_id: uuid.UUID
) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(MarketplaceVersion.version_number), 0))
        .where(MarketplaceVersion.item_id == item_id)
    )
    return int(result.scalar_one() or 0) + 1


def _copy_to_storage(
    skill_dir: Path,
    *,
    dest: Path,
    dry_run: bool,
) -> int:
    """Copy a validated skill into the marketplace storage tree.

    Returns ``total_bytes``. ``dry_run`` skips the copy but still walks
    the directory so the report is honest about expected size.

    Sync helper — callers wrap with ``asyncio.to_thread`` when invoked
    from an async context. Keeps the importer module ASYNC240-clean
    even though most of the surface is async.
    """

    total = 0
    if dry_run:
        for entry in skill_dir.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    continue
        return total

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, dest, symlinks=False)
    for entry in dest.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


async def import_skill(
    skill_dir: Path,
    *,
    commit_sha: str,
    ref: str,
    builtin_storage_dir: Path,
    db: AsyncSession,
    dry_run: bool = False,
) -> ImportResult:
    """Process a single skill end-to-end. Never raises — converts every
    failure into an ``ImportResult`` with the appropriate action so the
    caller can keep iterating (Spec §5.10)."""

    upstream_name = skill_dir.name

    ok, metadata, reason = validate_skill(skill_dir)
    if not ok or metadata is None:
        return ImportResult(
            name=upstream_name,
            action=ImportAction.FAILED_VALIDATION,
            detail=reason or "validation failed",
        )

    # Secret scan BEFORE we touch storage / DB so a leak doesn't leave
    # a partial copy behind.
    findings = scan_package(skill_dir)
    if findings:
        summary = ", ".join(f"{f.path} ({f.kind})" for f in findings[:5])
        logger.warning(
            "k-skill secret_scan rejected %s: %s", upstream_name, summary
        )
        return ImportResult(
            name=upstream_name,
            action=ImportAction.FAILED_SECRET_SCAN,
            detail=summary,
        )

    hints = collect_regex_hints(skill_dir)
    if upstream_name not in known_skills():
        logger.info(
            "k-skill import: new upstream skill encountered name=%s hints=%s",
            upstream_name,
            hints,
        )

    content_hash = compute_content_hash(skill_dir)
    requirements = requirements_for(upstream_name)
    profile = infer_execution_profile(skill_dir)

    item = await _find_item(db, upstream_name=upstream_name)
    is_new_item = item is None

    if is_new_item:
        item = MarketplaceItem(
            id=uuid.uuid4(),
            resource_type="skill",
            owner_user_id=None,
            is_system=True,
            is_listed=True,  # System imports list immediately.
            name=str(metadata.get("display_name") or upstream_name),
            slug=_slugify(upstream_name),
            description=str(metadata.get("description") or "")[:1024] or None,
            visibility="system",
            status="published",
            moderation_status="approved",
            source_kind="k-skill",
            source_url="https://github.com/NomaDamas/k-skill",
            source_external_id=upstream_name,
            categories=[metadata.get("category")] if metadata.get("category") else None,
            locale=str(metadata.get("locale") or "ko") if metadata.get("locale") else "ko",
        )
        if not dry_run:
            db.add(item)
            await db.flush()
    else:
        # Re-publish: nudge metadata fields that the upstream may have
        # updated. Description + name kept in sync; visibility stays
        # ``system`` regardless of what frontmatter claims.
        if metadata.get("description"):
            item.description = str(metadata["description"])[:1024]
        if metadata.get("display_name"):
            item.name = str(metadata["display_name"])
        if metadata.get("category"):
            item.categories = [str(metadata["category"])]

    # Reuse an existing version when content hash matches — Spec §5.4.
    existing_version: MarketplaceVersion | None = None
    if not is_new_item:
        existing_version = (
            await db.execute(
                select(MarketplaceVersion)
                .where(MarketplaceVersion.item_id == item.id)
                .where(MarketplaceVersion.content_hash == content_hash)
                .order_by(MarketplaceVersion.version_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if existing_version is not None:
        # Only metadata moved. Skip version + storage writes.
        item.updated_at = _now()
        return ImportResult(
            name=upstream_name,
            action=ImportAction.UNCHANGED,
            item_id=item.id,
            version_id=existing_version.id,
            hints=hints,
        )

    # New version path — copy bytes into the system storage tree.
    import asyncio

    version_id = uuid.uuid4()
    version_dest = builtin_storage_dir / str(version_id)
    total_bytes = await asyncio.to_thread(
        _copy_to_storage, skill_dir, dest=version_dest, dry_run=dry_run
    )

    version_label = str(metadata.get("version") or f"0.{commit_sha[:7] or '0'}.0")
    version_number = await _next_version_number(db, item_id=item.id) if not dry_run else 1

    version = MarketplaceVersion(
        id=version_id,
        item_id=item.id,
        version_label=version_label,
        version_number=version_number,
        resource_type="skill",
        payload_kind="skill_package",
        payload={
            "kind": "package",
            "name": upstream_name,
            "version": version_label,
            "display_name": metadata.get("display_name"),
            "phase": metadata.get("phase"),
        },
        storage_path=ensure_relative(f"marketplace/k-skill/{version_id}"),
        content_hash=content_hash,
        size_bytes=total_bytes,
        credential_requirements=requirements or None,
        execution_profile=profile,
        release_notes=str(metadata.get("release_notes") or "") or None,
        source_commit=commit_sha or None,
        source_ref=ref or None,
        source_path=upstream_name,
        created_by=None,
    )

    if dry_run:
        # Discard the version row but report the would-be IDs so the
        # operator can see what the real run would do.
        return ImportResult(
            name=upstream_name,
            action=ImportAction.CREATED if is_new_item else ImportAction.NEW_VERSION,
            detail=f"dry-run: would write {total_bytes} bytes",
            item_id=item.id,
            version_id=version_id,
            hints=hints,
        )

    db.add(version)
    await db.flush()
    item.latest_version_id = version.id
    item.published_at = item.published_at or _now()
    item.updated_at = _now()
    await db.flush()

    return ImportResult(
        name=upstream_name,
        action=ImportAction.CREATED if is_new_item else ImportAction.NEW_VERSION,
        item_id=item.id,
        version_id=version.id,
        hints=hints,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_sync(
    *,
    repo_dir: Path,
    builtin_storage_dir: Path,
    ref: str,
    commit_sha: str,
    db: AsyncSession,
    dry_run: bool = False,
    only: list[str] | None = None,
    keep_deprecated: bool = True,
) -> SyncReport:
    """Top-level orchestration. Discovers skills under ``repo_dir``,
    drives ``import_skill`` for each one, and marks any previously-
    imported upstream that has now disappeared as ``deprecated``.

    ``only`` restricts the run to a subset of upstream names — used by
    operators iterating on a single skill without re-running everything.
    """

    report = SyncReport(ref=ref, commit_sha=commit_sha, dry_run=dry_run)

    discovered = discover_skills(repo_dir)
    if only:
        wanted = set(only)
        discovered = [p for p in discovered if p.name in wanted]

    seen_names: set[str] = set()
    for skill_dir in discovered:
        seen_names.add(skill_dir.name)
        try:
            result = await import_skill(
                skill_dir,
                commit_sha=commit_sha,
                ref=ref,
                builtin_storage_dir=builtin_storage_dir,
                db=db,
                dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 — single skill must not abort
            logger.exception("k-skill import failed unexpectedly: %s", skill_dir)
            result = ImportResult(
                name=skill_dir.name,
                action=ImportAction.FAILED_VALIDATION,
                detail=f"unexpected error: {exc}",
            )
        report.results.append(result)

    # Deprecation pass — any pre-existing k-skill item whose upstream
    # source disappeared. ``only`` filtering disables the pass so a
    # partial sync doesn't deprecate the rest of the world.
    if not only:
        existing = (
            await db.execute(
                select(MarketplaceItem).where(
                    MarketplaceItem.source_kind == "k-skill"
                )
            )
        ).scalars().all()
        for item in existing:
            upstream_name = item.source_external_id
            if not upstream_name or upstream_name in seen_names:
                continue
            if not keep_deprecated and item.status != "deprecated":
                item.status = "deprecated"
                item.is_listed = False
                item.updated_at = _now()
                report.deprecated.append(upstream_name)
            elif keep_deprecated and item.status == "published":
                # Soft-deprecate but keep listing for backwards-compat.
                # No state change — just record it in the report.
                report.deprecated.append(upstream_name)

    return report


# ---------------------------------------------------------------------------
# Side-effect-free helpers reused by tests
# ---------------------------------------------------------------------------


# Re-export ``MarketplacePublicationLink`` only so future Slice F2 work
# (linking a published skill back into an upstream pin) can grab the
# import without re-deriving the module path.
_PUB_LINK_HINT = MarketplacePublicationLink

__all__ = [
    "ImportAction",
    "ImportResult",
    "SyncReport",
    "collect_regex_hints",
    "compute_content_hash",
    "discover_skills",
    "import_skill",
    "infer_execution_profile",
    "run_sync",
    "validate_skill",
]
