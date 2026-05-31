"""Marketplace publish flow (ADR-017 Slice C / Spec §6, §10.4).

Three entry points exposed to ``routers/marketplace``:

* ``publish_skill`` — Publish a user-owned skill as a new marketplace
  item OR add a new version to an existing one (same body shape;
  ``item_id`` distinguishes). Returns the resulting item.
* ``patch_item`` — Metadata-only edit on an existing item the caller
  manages.
* ``replace_acl`` / ``remove_acl_entry`` — Restricted-visibility
  recipient list management.
* ``disable_item`` — Soft-disable an item (status → ``disabled``;
  installations stay valid but new installs are blocked).

The publish flow snapshots the skill's on-disk bytes into a dedicated
versions directory (``data/marketplace/versions/<version_id>/``) so a
later edit to the source skill doesn't mutate previously installed
copies (Spec §6.4 — immutability). ``content_hash`` reuse means
re-publishing an unchanged skill returns the existing version without
duplicating storage.

Routers commit; this layer flushes only.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.error_codes import (
    marketplace_acl_required,
    marketplace_invalid_package,
    marketplace_invalid_visibility,
    marketplace_item_not_found,
    marketplace_manage_forbidden,
    marketplace_secret_detected,
    skill_not_found,
)
from app.marketplace.access import can_manage_item
from app.marketplace.schemas import (
    MarketplaceItemACLIn,
    MarketplaceItemPatchIn,
    PublishSkillIn,
)
from app.marketplace.secret_scan import scan_package
from app.models.marketplace import (
    MarketplaceItem,
    MarketplaceItemACL,
    MarketplacePublicationLink,
    MarketplaceVersion,
)
from app.models.skill import Skill
from app.storage.paths import ensure_relative, resolve_data_path

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------


def _versions_storage_root() -> Path:
    """Root for immutable version snapshots — distinct from per-user
    ``data/skills/`` so deleting a user-owned skill never strands an
    installed copy elsewhere (Spec §6.4). Mirrors the relative form
    ``skills/_marketplace_versions/<vid>`` stored in the column."""

    return (Path(settings.data_root) / "skills" / "_marketplace_versions").resolve()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _slugify(value: str) -> str:
    base = value.strip().lower().replace("_", "-").replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9-]+", "-", base).strip("-")
    return cleaned or "item"


# ---------------------------------------------------------------------------
# Internal: snapshot the skill payload
# ---------------------------------------------------------------------------


async def _snapshot_skill(
    skill: Skill, *, dest: Path
) -> tuple[Path, int, dict]:
    """Copy the skill's on-disk state into ``dest``.

    Returns ``(snapshot_path, total_bytes, payload_metadata)``.

    * package-kind → ``copytree`` of the directory.
    * text-kind   → copy the SKILL.md file into ``dest/SKILL.md``.

    ``payload_metadata`` carries kind + name + version so install can
    reconstruct the skill row without re-reading SKILL.md (Spec §6.2).
    """

    if not skill.storage_path:
        raise marketplace_invalid_package(
            f"skill {skill.id} has no on-disk storage to publish"
        )

    src = resolve_data_path(skill.storage_path)
    exists, is_file = await asyncio.to_thread(_probe_path, src)
    if not exists:
        raise marketplace_invalid_package(
            f"skill storage missing on disk: {skill.storage_path}"
        )

    await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
    try:
        if is_file:
            await asyncio.to_thread(_copy_text, src, dest)
        else:
            await asyncio.to_thread(shutil.copytree, src, dest, symlinks=False)
    except (OSError, shutil.Error) as exc:
        await asyncio.to_thread(shutil.rmtree, dest, ignore_errors=True)
        raise marketplace_invalid_package(f"snapshot failed: {exc}") from exc

    total_bytes = await asyncio.to_thread(_dir_size, dest)
    payload_metadata = {
        "kind": skill.kind,
        "name": skill.name,
        "slug": skill.slug,
        "version": skill.version,
    }
    return dest, total_bytes, payload_metadata


def _probe_path(p: Path) -> tuple[bool, bool]:
    if not p.exists():
        return False, False
    return True, p.is_file()


def _copy_text(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest / "SKILL.md")


def _dir_size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    total = 0
    for entry in p.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


async def _next_version_number(
    db: AsyncSession, item_id: uuid.UUID
) -> int:
    """Item-scoped monotonic version counter (uq_marketplace_versions_item_number)."""

    result = await db.execute(
        select(func.coalesce(func.max(MarketplaceVersion.version_number), 0))
        .where(MarketplaceVersion.item_id == item_id)
    )
    current = int(result.scalar_one() or 0)
    return current + 1


async def _scan_or_raise(snapshot_path: Path) -> None:
    findings = await asyncio.to_thread(scan_package, snapshot_path)
    if findings:
        summary = ", ".join(f"{f.path} ({f.kind})" for f in findings[:5])
        raise marketplace_secret_detected(
            f"package contains potential secrets: {summary}"
        )


async def _create_acl(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    user_ids: list[uuid.UUID],
    permission: str = "install",
) -> None:
    """Wipe + set ACL rows for an item. Idempotent — used on first
    publish and on ACL replace. CASCADE cleans up dropped recipients."""

    existing = (
        await db.execute(
            select(MarketplaceItemACL).where(MarketplaceItemACL.item_id == item.id)
        )
    ).scalars().all()
    for row in existing:
        await db.delete(row)
    for user_id in user_ids:
        db.add(
            MarketplaceItemACL(
                item_id=item.id, user_id=user_id, permission=permission
            )
        )


async def _upsert_publication_link(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    skill: Skill,
    user_id: uuid.UUID,
) -> None:
    """Create or update the back-reference row used by
    ``ResourcePublicationSummaryOut`` derivation."""

    stmt = select(MarketplacePublicationLink).where(
        MarketplacePublicationLink.item_id == item.id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        db.add(
            MarketplacePublicationLink(
                user_id=user_id,
                item_id=item.id,
                resource_type="skill",
                source_skill_id=skill.id,
            )
        )
        return
    row.user_id = user_id
    row.source_skill_id = skill.id
    row.resource_type = "skill"
    row.updated_at = _now()


async def publish_skill(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user: CurrentUser,
    body: PublishSkillIn,
) -> MarketplaceItem:
    """Spec §6.2 — publish or version-bump a skill.

    Handles both the "new item" path (``body.item_id`` is ``None``) and
    the "new version on existing item" path. The on-disk snapshot is
    deduplicated by content_hash so re-publishing an unchanged skill
    returns the previous version.
    """

    skill = await db.get(Skill, skill_id)
    if skill is None or skill.user_id != user.id:
        # Ownership check first — enumeration safety.
        raise skill_not_found()

    if body.visibility not in ("private", "restricted", "public", "unlisted"):
        raise marketplace_invalid_visibility(
            f"unsupported publish visibility: {body.visibility}"
        )
    if body.visibility == "restricted" and not body.acl_user_ids:
        # Defence-in-depth — the Pydantic validator already rejects
        # this shape, but a hand-crafted call site could bypass it.
        raise marketplace_acl_required()

    # Resolve / create the item row -----------------------------------------
    item: MarketplaceItem | None = None
    if body.item_id is not None:
        item = await db.get(MarketplaceItem, body.item_id)
        if item is None:
            raise marketplace_item_not_found()
        if not can_manage_item(item, user):
            raise marketplace_manage_forbidden()
    else:
        # ``body.item_id`` 없음 — 신규 publish 흐름이지만 ``(owner, slug)``
        # UNIQUE constraint를 침해하지 않으려면 동일 owner+slug 기존 item을
        # 먼저 검색해 재사용해야 한다. 사용자가 자기 skill을 publish→삭제→
        # 재publish 할 때 publication_link 는 CASCADE 로 사라지지만
        # marketplace_items 자체는 남기 때문에, naive insert 는 IntegrityError
        # 로 500 을 던지고 CORS 헤더가 누락되어 브라우저는 "네트워크 오류"
        # 처럼 표시한다.
        item_slug = _slugify(body.name)
        existing = (
            await db.execute(
                select(MarketplaceItem)
                .where(MarketplaceItem.owner_user_id == user.id)
                .where(MarketplaceItem.resource_type == "skill")
                .where(MarketplaceItem.slug == item_slug)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 기존 item 재사용 — 새 version 을 추가하는 흐름으로 자연스럽게
            # 합류. metadata 갱신은 아래 ``if body.item_id is None`` 분기에서
            # name/description/tags/categories 를 다시 채운다.
            if not can_manage_item(existing, user):
                # 동일 slug 가 시스템 item 등에 잡혔을 때의 방어선.
                raise marketplace_manage_forbidden()
            item = existing
        else:
            item = MarketplaceItem(
                id=uuid.uuid4(),
                resource_type="skill",
                owner_user_id=user.id,
                is_system=False,
                is_listed=False,  # Spec §0.1 — public items start unlisted.
                name=body.name,
                slug=item_slug,
                description=body.description,
                icon_id=body.icon_id,
                visibility=body.visibility,
                status="draft",
                moderation_status="approved",
                source_kind="user",
                tags=list(body.tags) or None,
                categories=list(body.categories) or None,
            )
            db.add(item)
            await db.flush()

    # Snapshot the skill payload off disk into the immutable
    # versions storage. Use a temp directory so a secret_scan failure
    # cleans up cleanly without leaving partial data.
    version_id = uuid.uuid4()
    snapshot_target = _versions_storage_root() / str(version_id)
    tmp_target = snapshot_target.with_suffix(".publish.tmp")
    if await asyncio.to_thread(tmp_target.exists):
        await asyncio.to_thread(shutil.rmtree, tmp_target, ignore_errors=True)

    snapshot_path, total_bytes, payload = await _snapshot_skill(
        skill, dest=tmp_target
    )
    # Compute the canonical content hash for dedup.
    hash_obj = hashlib.sha256()
    if snapshot_path.is_file():
        hash_obj.update(snapshot_path.read_bytes())
    else:
        for entry in sorted(snapshot_path.rglob("*")):
            if entry.is_file():
                hash_obj.update(entry.relative_to(snapshot_path).as_posix().encode())
                hash_obj.update(b"\0")
                try:
                    hash_obj.update(entry.read_bytes())
                except OSError:
                    continue
    content_hash = hash_obj.hexdigest()

    # Secret_scan AFTER snapshot, so we scan the exact bytes the install
    # path will receive (Spec §13.1).
    try:
        await _scan_or_raise(snapshot_path)
    except Exception:
        await asyncio.to_thread(shutil.rmtree, tmp_target, ignore_errors=True)
        raise

    # Dedup against the most recent version on this item.
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
        # Re-use the prior snapshot — discard the tmp copy.
        await asyncio.to_thread(shutil.rmtree, tmp_target, ignore_errors=True)
        version = existing_version
    else:
        version_number = await _next_version_number(db, item.id)
        version_label = skill.version or f"0.{version_number}.0"
        version = MarketplaceVersion(
            id=version_id,
            item_id=item.id,
            version_label=version_label,
            version_number=version_number,
            resource_type="skill",
            payload_kind="skill_package",
            payload=payload,
            storage_path=ensure_relative(
                f"skills/_marketplace_versions/{version_id}"
            ),
            content_hash=content_hash,
            size_bytes=total_bytes,
            credential_requirements=skill.credential_requirements,
            execution_profile=skill.execution_profile,
            release_notes=body.release_notes,
            source_commit=skill.source_commit,
            created_by=user.id,
        )
        db.add(version)
        await db.flush()
        # Atomic rename only after the row is committed-to (flushed).
        if await asyncio.to_thread(snapshot_target.exists):
            await asyncio.to_thread(
                shutil.rmtree, snapshot_target, ignore_errors=True
            )
        await asyncio.to_thread(tmp_target.rename, snapshot_target)

    # Update item metadata: visibility, name (if not bumping an existing item
    # without explicit name change), pointer to latest version, status.
    item.latest_version_id = version.id
    item.visibility = body.visibility
    item.status = "published"
    item.published_at = _now()
    item.updated_at = _now()
    # First publish — capture name / description / tags supplied in the body.
    if body.item_id is None:
        item.name = body.name
        item.slug = _slugify(body.name)
        item.description = body.description
        item.icon_id = body.icon_id
        item.tags = list(body.tags) or None
        item.categories = list(body.categories) or None
    else:
        # Re-publish: leave name/slug alone (use PATCH for metadata
        # edits), but allow visibility / description bumps via this
        # path so a private-→-public transition doesn't need two calls.
        if body.description is not None:
            item.description = body.description
        if body.icon_id is not None:
            item.icon_id = body.icon_id
        if body.tags:
            item.tags = list(body.tags)
        if body.categories:
            item.categories = list(body.categories)

    if body.visibility == "restricted":
        await _create_acl(
            db, item=item, user_ids=list(body.acl_user_ids), permission="install"
        )
    else:
        # Visibility transitioned away from restricted — drop the ACL.
        existing = (
            await db.execute(
                select(MarketplaceItemACL).where(
                    MarketplaceItemACL.item_id == item.id
                )
            )
        ).scalars().all()
        for row in existing:
            await db.delete(row)

    await _upsert_publication_link(
        db, item=item, skill=skill, user_id=user.id
    )

    await db.flush()
    logger.info(
        "marketplace.publish user=%s item=%s version=%s hash=%s",
        user.id,
        item.id,
        version.id,
        content_hash[:8],
    )
    return item


# ---------------------------------------------------------------------------
# Item management
# ---------------------------------------------------------------------------


async def patch_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user: CurrentUser,
    body: MarketplaceItemPatchIn,
) -> MarketplaceItem:
    """Metadata-only patch (Spec §10.4). No version mutation."""

    item = await db.get(MarketplaceItem, item_id)
    if item is None:
        raise marketplace_item_not_found()
    if not can_manage_item(item, user):
        raise marketplace_manage_forbidden()

    if body.name is not None:
        item.name = body.name
    if body.description is not None:
        item.description = body.description
    if body.icon_id is not None:
        item.icon_id = body.icon_id
    if body.icon_url is not None:
        item.icon_url = body.icon_url
    if body.tags is not None:
        item.tags = list(body.tags) or None
    if body.categories is not None:
        item.categories = list(body.categories) or None
    if body.locale is not None:
        item.locale = body.locale

    if body.visibility is not None and body.visibility != item.visibility:
        await _apply_visibility_change(db, item=item, next_visibility=body.visibility)

    item.updated_at = _now()
    return item


async def _apply_visibility_change(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    next_visibility: str,
) -> None:
    """Owner-initiated visibility 전환 가드.

    - ``restricted`` 로 전환 시 ACL 1명 이상 필수 — 빈 ACL 은 의미 모순.
    - ``public`` 에서 비공개로 떨굴 때 ``is_listed=False`` 도 같이 떨궈 카탈로그
      검색 권한 회수 (PRD §11.7 — listing approve 권한은 super_user 책임).
    """

    if next_visibility == "restricted":
        existing = await db.execute(
            select(MarketplaceItemACL).where(MarketplaceItemACL.item_id == item.id)
        )
        if existing.scalars().first() is None:
            raise marketplace_acl_required()

    if item.visibility == "public" and next_visibility != "public":
        item.is_listed = False

    item.visibility = next_visibility  # type: ignore[assignment]


async def replace_acl(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user: CurrentUser,
    body: MarketplaceItemACLIn,
) -> MarketplaceItem:
    """Replace the ACL recipient list. Restricted items need ≥1 row;
    otherwise the operation is a noop replace."""

    item = await db.get(MarketplaceItem, item_id)
    if item is None:
        raise marketplace_item_not_found()
    if not can_manage_item(item, user):
        raise marketplace_manage_forbidden()
    if item.visibility == "restricted" and not body.user_ids:
        raise marketplace_acl_required()

    await _create_acl(
        db,
        item=item,
        user_ids=list(body.user_ids),
        permission=body.permission,
    )
    item.updated_at = _now()
    return item


async def remove_acl_entry(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user_id_to_remove: uuid.UUID,
    user: CurrentUser,
) -> None:
    """Drop a single user from the ACL.

    Refuses to leave a restricted item with zero ACL rows — caller
    should change visibility first."""

    item = await db.get(MarketplaceItem, item_id)
    if item is None:
        raise marketplace_item_not_found()
    if not can_manage_item(item, user):
        raise marketplace_manage_forbidden()

    rows = (
        await db.execute(
            select(MarketplaceItemACL).where(
                MarketplaceItemACL.item_id == item.id
            )
        )
    ).scalars().all()
    remaining = [r for r in rows if r.user_id != user_id_to_remove]
    if item.visibility == "restricted" and not remaining:
        raise marketplace_acl_required()

    for row in rows:
        if row.user_id == user_id_to_remove:
            await db.delete(row)
    item.updated_at = _now()


async def disable_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user: CurrentUser,
) -> MarketplaceItem:
    """Spec §10.4 — soft disable. Existing installations stay but new
    installs are blocked via ``can_install_item``."""

    item = await db.get(MarketplaceItem, item_id)
    if item is None:
        raise marketplace_item_not_found()
    if not can_manage_item(item, user):
        raise marketplace_manage_forbidden()
    item.status = "disabled"
    item.is_listed = False
    item.updated_at = _now()
    return item


async def enable_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user: CurrentUser,
) -> MarketplaceItem:
    """Disable 의 inverse — ``status: disabled → published``. ``is_listed`` 는
    그대로 False 유지 (public 카탈로그 노출은 super_user approve 가 별도로
    잡는다 — PRD §11.7).

    disabled 가 아닌 상태에서 enable 호출은 idempotent no-op (반환 그대로).
    """

    item = await db.get(MarketplaceItem, item_id)
    if item is None:
        raise marketplace_item_not_found()
    if not can_manage_item(item, user):
        raise marketplace_manage_forbidden()
    if item.status == "disabled":
        item.status = "published"
        item.updated_at = _now()
    return item


__all__ = [
    "disable_item",
    "enable_item",
    "patch_item",
    "publish_skill",
    "remove_acl_entry",
    "replace_acl",
]
