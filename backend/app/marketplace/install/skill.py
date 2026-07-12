"""Skill-type install / overwrite logic — BE-S3 split of
``install_service``.

``_install_skill_item`` is the "create new install" section extracted
verbatim from ``install_service.install_item`` (the reuse/overwrite
pre-dispatch stays in the facade because it calls
``_remove_install_artifacts``, which lives there).
``_overwrite_skill_installation`` is the skill tail of
``update_installation`` extracted verbatim (type/None guards stay in
the facade). Everything else is moved verbatim; only import plumbing
changed.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.error_codes import marketplace_credential_required
from app.marketplace import credential_requirements
from app.marketplace.install.bindings import _persist_bindings
from app.marketplace.install.common import (
    _derive_origin,
    _now,
    _payload_skill_kind,
    _slugify,
)
from app.marketplace.install.snapshot import (
    _copy_snapshot,
    _rel_install_storage,
    _replace_skill_snapshot,
    _target_for,
)
from app.marketplace.schemas import InstallMarketplaceItemIn
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceVersion,
)
from app.models.skill import Skill
from app.storage.paths import ensure_relative

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


def _apply_version_metadata_to_skill(
    *,
    skill: Skill,
    item: MarketplaceItem,
    version: MarketplaceVersion,
) -> None:
    """Refresh DB metadata after an in-place marketplace overwrite."""

    payload = version.payload or {}
    skill.description = item.description
    skill.kind = _payload_skill_kind(version)
    skill.storage_path = ensure_relative(_rel_install_storage(skill.id, version))
    skill.content_hash = version.content_hash
    skill.size_bytes = int(version.size_bytes or 0)
    skill.version = payload.get("version")
    skill.package_metadata = payload
    skill.is_system = item.is_system
    skill.source_kind = item.source_kind
    skill.source_marketplace_item_id = item.id
    skill.source_marketplace_version_id = version.id
    skill.source_commit = version.source_commit
    skill.credential_requirements = version.credential_requirements
    skill.execution_profile = version.execution_profile
    skill.origin_marketplace_item_id = item.id
    skill.origin_marketplace_version_id = version.id
    skill.is_dirty = False
    skill.last_modified_at = _now()


async def _install_skill_item(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    version: MarketplaceVersion,
    user: CurrentUser,
    body: InstallMarketplaceItemIn,
) -> MarketplaceInstallation:
    """Create a fresh skill row + installation from ``version``.

    Extracted verbatim from ``install_item`` (BE-S3) — the caller has
    already handled permission checks, version resolution, the install
    lock and the reuse/overwrite pre-dispatch.
    """

    # ----- create new install -------------------------------------------
    skill_id = uuid.uuid4()
    target = _target_for(skill_id)
    tmp = target.with_suffix(".install.tmp")
    if tmp.exists():
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)

    await _copy_snapshot(version, tmp)

    payload = version.payload or {}
    name = body.name_override or payload.get("name") or item.name
    origin_kind, origin_user_id = _derive_origin(item, user)

    skill = Skill(
        id=skill_id,
        user_id=user.id,
        name=name,
        slug=_slugify(name),
        description=item.description,
        kind=_payload_skill_kind(version),
        storage_path=ensure_relative(_rel_install_storage(skill_id, version)),
        content_hash=version.content_hash,
        size_bytes=int(version.size_bytes or 0),
        version=payload.get("version"),
        package_metadata=payload,
        used_by_count=0,
        is_system=item.is_system,
        source_kind=item.source_kind,
        source_marketplace_item_id=item.id,
        source_marketplace_version_id=version.id,
        source_commit=version.source_commit,
        credential_requirements=version.credential_requirements,
        execution_profile=version.execution_profile,
        origin_kind=origin_kind,
        origin_user_id=origin_user_id,
        origin_marketplace_item_id=item.id,
        origin_marketplace_version_id=version.id,
        is_dirty=False,
        last_modified_at=_now(),
    )
    db.add(skill)
    try:
        await db.flush()
    except Exception:
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)
        raise

    # Bind credentials (validate_binding rejects mismatches → 422).
    try:
        await _persist_bindings(db, skill=skill, user=user, bindings=body.credential_bindings)
    except Exception:
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)
        raise

    # Determine install_status. ``reject`` mode = caller wants a hard
    # error when something's missing; ``needs_setup`` mode = soft state.
    missing = await credential_requirements.missing_required_keys(db, skill=skill, user=user)
    if missing and body.install_missing_credentials == "reject":
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)
        raise marketplace_credential_required(
            f"missing required credential bindings: {', '.join(missing)}"
        )
    install_status = "needs_setup" if missing else "active"

    installation = MarketplaceInstallation(
        id=uuid.uuid4(),
        user_id=user.id,
        item_id=item.id,
        version_id=version.id,
        resource_type="skill",
        installed_skill_id=skill.id,
        install_status=install_status,
        is_dirty=False,
        installed_at=_now(),
    )
    db.add(installation)
    try:
        await db.flush()
    except Exception:
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)
        raise

    # Atomic rename — the request only "succeeds" once the directory is
    # in its final location. Anything that needs a rollback past this
    # point must remove ``target`` explicitly.
    if target.exists():
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
    await asyncio.to_thread(tmp.rename, target)

    return installation


async def _overwrite_skill_installation(
    db: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    item: MarketplaceItem,
    latest: MarketplaceVersion,
    user: CurrentUser,
    skill: Skill,
) -> MarketplaceInstallation:
    """``overwrite`` update strategy for skill installations.

    Extracted verbatim from ``update_installation`` (BE-S3) — the caller
    has already resolved ``installation``/``item``/``latest``, loaded the
    installed ``skill`` row and handled the dirty/type guards.
    """

    await _replace_skill_snapshot(latest, skill)
    _apply_version_metadata_to_skill(skill=skill, item=item, version=latest)
    missing = await credential_requirements.missing_required_keys(db, skill=skill, user=user)
    installation.version_id = latest.id
    installation.install_status = "needs_setup" if missing else "active"
    installation.is_dirty = False
    installation.updated_at = _now()
    return installation
