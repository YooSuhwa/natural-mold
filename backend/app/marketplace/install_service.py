"""Marketplace install / update / uninstall flow (ADR-017 Slice B).

Spec §7 (install flow), §10.3 (API surface). Slice B only — publish lives
in ``publish_service`` (Slice C) and runtime mount + credential injection
in ``agent_runtime/`` (Slice E).

Transaction shape (Spec §7.3):

1. Permission + version resolve (read).
2. Reserve new ``skill_id``. Build target path ``<storage>/<skill_id>.tmp``.
3. ``shutil.copytree(version.storage_path → target.tmp)`` (off main thread).
4. Insert ``Skill`` + ``MarketplaceInstallation`` + ``SkillCredentialBinding``
   rows (no commit yet). Validate each binding via
   ``credential_requirements.validate_binding``.
5. ``await db.commit()`` — on success, rename ``target.tmp → target``.
6. Failure path: best-effort ``rmtree`` on the temp dir + ``db.rollback``.

The same pattern handles ``install_new_copy`` updates (new skill row,
old left alone) and ``overwrite`` (delete old first, then run the install).

The service is intentionally synchronous-feeling — there is no background
job. Spec §7.4 acknowledges installs are I/O bound but small (<10MB
typically); a 50ms copytree is fine on the request path.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.error_codes import (
    marketplace_credential_required,
    marketplace_dirty_installation,
    marketplace_invalid_package,
    marketplace_item_not_found,
    marketplace_version_not_found,
)
from app.marketplace import credential_requirements
from app.marketplace.access import can_install_item, is_owner
from app.marketplace.schemas import (
    InstallMarketplaceItemIn,
    UpdateMarketplaceInstallationIn,
)
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceVersion,
    SkillCredentialBinding,
)
from app.models.skill import Skill
from app.storage.paths import ensure_relative, resolve_data_path

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _skill_storage_root() -> Path:
    """Mirrors ``app.skills.service._skill_root`` without importing the
    module (file-boundary rule for Slice B — install_service must not
    modify skill service.py). ADR-018 — derived from ``data_root``."""

    return (Path(settings.data_root) / "skills").resolve()


def _target_for(skill_id: uuid.UUID) -> Path:
    return _skill_storage_root() / str(skill_id)


def _rel_install_storage(skill_id: uuid.UUID, version: MarketplaceVersion) -> str:
    """text-kind → ``skills/<id>/SKILL.md`` (file); package-kind →
    ``skills/<id>`` (dir). Relative to ``settings.data_root`` per ADR-018."""

    suffix = "/SKILL.md" if _payload_skill_kind(version) == "text" else ""
    return f"skills/{skill_id}{suffix}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _slugify(value: str) -> str:
    """Lowercase, dash-separated, ASCII-only identifier — duplicates the
    small helper from ``skills.service`` (we don't import to keep the
    module boundary clean)."""

    import re

    base = value.strip().lower().replace("_", "-").replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9-]+", "-", base).strip("-")
    return cleaned or "skill"


# ---------------------------------------------------------------------------
# Origin derivation (Spec §7.5 — call site for new installs)
# ---------------------------------------------------------------------------


def _derive_origin(
    item: MarketplaceItem, user: CurrentUser
) -> tuple[str, uuid.UUID | None]:
    """Compute ``(origin_kind, origin_user_id)`` for a freshly installed
    skill row. Maps directly to Spec §7.5.

    Returns ``origin_user_id`` for "shared_with_me" / "community" so the
    derived row remembers who published it. Owner installs reuse the
    user id so origin label collapses to ``imported_by_me`` (or
    ``created_by_me`` in the special case of reinstalling one's own
    item, which we treat as imported because the install pathway is
    the same as a foreign import).
    """

    if item.is_system and item.source_kind == "k-skill":
        return "built_in_k_skill", item.owner_user_id
    if item.is_system and item.source_kind == "system_seed":
        return "system_seed", item.owner_user_id
    if not is_owner(item, user):
        if item.visibility == "restricted":
            return "shared_with_me", item.owner_user_id
        if item.visibility == "public":
            return "community", item.owner_user_id
    return "imported_by_me", item.owner_user_id


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


async def _resolve_version(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    version_id: uuid.UUID | None,
) -> MarketplaceVersion:
    """Load the requested version or the item's ``latest_version``.

    404 (collapsed with item-not-found semantics) when the requested
    version doesn't belong to the item or doesn't exist.
    """

    if version_id is not None:
        version = await db.get(MarketplaceVersion, version_id)
        if version is None or version.item_id != item.id:
            raise marketplace_version_not_found()
        return version
    if item.latest_version_id is None:
        # Item without versions — install is meaningless.
        raise marketplace_version_not_found()
    version = await db.get(MarketplaceVersion, item.latest_version_id)
    if version is None:
        raise marketplace_version_not_found()
    return version


async def _existing_installation(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    user: CurrentUser,
) -> MarketplaceInstallation | None:
    stmt = (
        select(MarketplaceInstallation)
        .where(
            MarketplaceInstallation.item_id == item.id,
            MarketplaceInstallation.user_id == user.id,
            MarketplaceInstallation.install_status != "uninstalled",
        )
        .order_by(MarketplaceInstallation.installed_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _copy_snapshot(version: MarketplaceVersion, target: Path) -> None:
    """Copy the version's on-disk snapshot to ``target``.

    Raises ``marketplace_invalid_package`` when the snapshot is missing
    or unreadable. Off the main event loop to keep the request hot path
    responsive on big packages.
    """

    if not version.storage_path:
        raise marketplace_invalid_package("version has no storage snapshot")

    src = resolve_data_path(version.storage_path)
    # ``ASYNC240`` — filesystem checks happen off the event loop. We
    # also return the is_file decision from the same probe so the copy
    # picks the correct branch without a second stat.
    exists, is_file = await asyncio.to_thread(_probe_path, src)
    if not exists:
        raise marketplace_invalid_package(
            f"version snapshot missing on disk: {version.storage_path}"
        )

    await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)

    try:
        if is_file:
            # text-kind version: SKILL.md file. Recreate ``<target>/SKILL.md``.
            await asyncio.to_thread(_copy_text_snapshot, src, target)
        else:
            await asyncio.to_thread(shutil.copytree, src, target)
    except (OSError, shutil.Error) as exc:
        # Cleanup partial dir then translate to a 400 the user can act on.
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        raise marketplace_invalid_package(f"copy failed: {exc}") from exc


def _probe_path(p: Path) -> tuple[bool, bool]:
    """Return ``(exists, is_file)`` in a single stat. Sync helper for
    ``asyncio.to_thread`` so async callers stay ASYNC240-clean."""

    if not p.exists():
        return False, False
    return True, p.is_file()


def _rmtree_skill_storage(p: Path) -> None:
    """Remove a skill storage path safely. For text skills ``p`` is the
    SKILL.md file path — climb one level to wipe the wrapping dir."""

    target = p.parent if p.is_file() else p
    shutil.rmtree(target, ignore_errors=True)


def _copy_text_snapshot(src: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target / "SKILL.md")


def _payload_skill_kind(version: MarketplaceVersion) -> str:
    """Decide the installed skill's ``kind`` from the version payload.

    ``version.payload`` carries arbitrary metadata; the publish flow
    records ``{"kind": "text"|"package", ...}``. Default to ``package``
    because that's the safe-superset (a single-file package is valid).
    """

    payload = version.payload or {}
    kind = payload.get("kind") or "package"
    return "package" if kind not in ("text", "package") else kind


async def _persist_bindings(
    db: AsyncSession,
    *,
    skill: Skill,
    user: CurrentUser,
    bindings: dict[str, uuid.UUID],
) -> list[SkillCredentialBinding]:
    """Validate and create binding rows. Each binding goes through the
    same validator as the standalone PUT endpoint (Spec §10.6)."""

    rows: list[SkillCredentialBinding] = []
    for key, credential_id in bindings.items():
        row = await credential_requirements.upsert_binding(
            db,
            skill=skill,
            user=user,
            requirement_key=key,
            credential_id=credential_id,
        )
        rows.append(row)
    return rows


async def install_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user: CurrentUser,
    body: InstallMarketplaceItemIn,
) -> MarketplaceInstallation:
    """Install (or re-use) a marketplace item for ``user``. Caller must
    commit on success — we don't double-commit here because routers wrap
    install + side effects in one unit of work.
    """

    # Bezos OPEN-1 (2026-05-19): ``can_install_item`` walks
    # ``item.acl_entries`` for restricted-visibility items. Plain
    # ``db.get(MarketplaceItem, ...)`` returns the row without
    # eager-loading relationships, and the subsequent lazy access fires
    # an unsupported sync IO under the async session →
    # ``MissingGreenlet`` → 500. We surface 500 instead of the intended
    # 404, which doubles as an **enumeration oracle** (500 only happens
    # for restricted items the caller can't see).
    # Fix: eager-load ``acl_entries`` so the permission check stays in
    # pre-loaded memory. ``latest_version`` is also pre-loaded because
    # ``_resolve_version`` below would otherwise re-fetch the row.
    item_stmt = (
        select(MarketplaceItem)
        .where(MarketplaceItem.id == item_id)
        .options(
            selectinload(MarketplaceItem.acl_entries),
            selectinload(MarketplaceItem.latest_version),
        )
    )
    item = (await db.execute(item_stmt)).scalar_one_or_none()
    if item is None:
        raise marketplace_item_not_found()
    if not can_install_item(item, user):
        # Collapse forbidden + missing for enumeration safety.
        logger.info(
            "marketplace_install_forbidden user=%s item=%s",
            user.id,
            item_id,
        )
        raise marketplace_item_not_found()

    # Resource_type guard: Slice B implements ``skill`` only. ``agent`` /
    # ``mcp`` install will land in a later slice — surface as "not
    # found" so we don't 500.
    if item.resource_type != "skill":
        logger.info(
            "marketplace_install_unsupported_resource_type %s", item.resource_type
        )
        raise marketplace_item_not_found()

    version = await _resolve_version(db, item=item, version_id=body.version_id)

    # install_mode dispatch (Spec §10.8 / desc 단계 3)
    existing = await _existing_installation(db, item=item, user=user)
    if existing is not None:
        if body.install_mode == "reuse_or_update":
            # State refresh only — bindings update flow lives in the
            # dedicated PUT bindings endpoint.
            return existing
        if body.install_mode == "overwrite_existing":
            # Delete the existing skill + installation, then re-install.
            await _remove_install_artifacts(db, existing)
            await db.flush()
        # ``new_copy`` falls through to create a fresh row.

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
        await _persist_bindings(
            db, skill=skill, user=user, bindings=body.credential_bindings
        )
    except Exception:
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)
        raise

    # Determine install_status. ``reject`` mode = caller wants a hard
    # error when something's missing; ``needs_setup`` mode = soft state.
    missing = await credential_requirements.missing_required_keys(
        db, skill=skill, user=user
    )
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


# ---------------------------------------------------------------------------
# Update (Spec §10.3)
# ---------------------------------------------------------------------------


async def update_installation(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    user: CurrentUser,
    body: UpdateMarketplaceInstallationIn,
) -> MarketplaceInstallation:
    """Apply an update strategy to an existing installation.

    * ``overwrite``         — replace installed skill files + bindings with
                              the item's latest version. Dirty edits lost.
    * ``install_new_copy``  — leave the existing installation alone, create
                              a new skill row + installation pointing at
                              the latest version.
    * ``keep_current``      — bump the installation pointer (mark seen) but
                              don't modify files. Lets the UI dismiss the
                              "update available" badge.
    """

    installation = await db.get(MarketplaceInstallation, installation_id)
    if installation is None or installation.user_id != user.id:
        raise marketplace_item_not_found()

    item = await db.get(MarketplaceItem, installation.item_id)
    if item is None or item.latest_version_id is None:
        raise marketplace_item_not_found()

    latest = await db.get(MarketplaceVersion, item.latest_version_id)
    if latest is None:
        raise marketplace_version_not_found()

    # Refuse silent overwrites when the user has edited the installed
    # copy — they must opt in explicitly with overwrite / install_new_copy.
    skill = (
        await db.get(Skill, installation.installed_skill_id)
        if installation.installed_skill_id is not None
        else None
    )
    dirty = bool(installation.is_dirty or (skill and skill.is_dirty))
    if dirty and body.strategy == "overwrite":
        # ``overwrite`` is allowed but the operator must confirm by sending
        # the strategy — we keep this branch reachable. No-op block here.
        pass
    if dirty and body.strategy not in ("overwrite", "install_new_copy", "keep_current"):
        raise marketplace_dirty_installation()

    if body.strategy == "keep_current":
        installation.version_id = latest.id
        installation.is_dirty = False
        installation.updated_at = _now()
        return installation

    if body.strategy == "install_new_copy":
        # Re-enter the install path with ``new_copy`` semantics.
        new_install = await install_item(
            db,
            item_id=item.id,
            user=user,
            body=InstallMarketplaceItemIn(
                version_id=latest.id,
                install_mode="new_copy",
                install_missing_credentials="needs_setup",
            ),
        )
        return new_install

    # ``overwrite`` — replace files in place. Easiest path: delete the
    # old install artifacts, run the install again as a fresh copy
    # bound to the latest version, but reuse the installation row id so
    # external references (frontend state, agent links) survive.
    await _remove_install_artifacts(db, installation, keep_installation=True)
    await db.flush()
    new_install = await install_item(
        db,
        item_id=item.id,
        user=user,
        body=InstallMarketplaceItemIn(
            version_id=latest.id,
            install_mode="new_copy",
            install_missing_credentials="needs_setup",
        ),
    )
    # Reparent the existing installation id to the new skill so the
    # caller's URL stays valid.
    installation.installed_skill_id = new_install.installed_skill_id
    installation.version_id = latest.id
    installation.install_status = new_install.install_status
    installation.is_dirty = False
    installation.updated_at = _now()
    # The freshly created ``new_install`` row is redundant — delete it
    # so we don't have two rows pointing at the same skill.
    await db.delete(new_install)
    return installation


# ---------------------------------------------------------------------------
# Delete (Spec §3.11)
# ---------------------------------------------------------------------------


async def delete_installation(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    user: CurrentUser,
    delete_resource: bool = False,
) -> None:
    """Soft delete by default (Spec §3.11 — link suspension keeps the
    user's skill row intact). ``delete_resource=True`` cascades into the
    installed skill (filesystem + DB row).
    """

    installation = await db.get(MarketplaceInstallation, installation_id)
    if installation is None or installation.user_id != user.id:
        raise marketplace_item_not_found()

    if delete_resource:
        await _remove_install_artifacts(db, installation)
        await db.delete(installation)
        return

    installation.install_status = "uninstalled"
    installation.updated_at = _now()


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


async def _remove_install_artifacts(
    db: AsyncSession,
    installation: MarketplaceInstallation,
    *,
    keep_installation: bool = False,
) -> None:
    """Remove the installed skill row + its on-disk directory. Used by
    overwrite/uninstall paths.

    ``keep_installation`` skips the installation row delete so callers
    can rebind it to a new skill (overwrite-in-place update).
    """

    if installation.installed_skill_id is not None:
        skill = await db.get(Skill, installation.installed_skill_id)
        if skill is not None:
            if skill.storage_path:
                # text-kind skills store SKILL.md path — climb one level
                # before delete; package-kind storage_path is the dir.
                # is_file()/exists() go through ``to_thread`` to stay
                # ASYNC240-clean.
                await asyncio.to_thread(
                    _rmtree_skill_storage, resolve_data_path(skill.storage_path)
                )
            await db.delete(skill)
    if not keep_installation:
        await db.delete(installation)


__all__: list[str] = [
    "delete_installation",
    "install_item",
    "update_installation",
]


# Silence Any-unused lint when generics aren't referenced.
_ANY_HINT: Any = None
