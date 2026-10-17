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
old left alone). ``overwrite`` updates the installed skill in place so
existing agent links keep pointing at the refreshed skill row.

The service is intentionally synchronous-feeling — there is no background
job. Spec §7.4 acknowledges installs are I/O bound but small (<10MB
typically); a 50ms copytree is fine on the request path.

BE-S3: this module is now the public facade + type dispatcher. The
type-specific bodies live in ``app.marketplace.install`` (``skill`` /
``mcp`` / ``agent_blueprint``) with shared helpers in ``common`` /
``snapshot`` / ``bindings``. External callers keep importing from here.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_scope import load_private_installation
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scoped
from app.error_codes import (
    marketplace_dirty_installation,
    marketplace_item_not_found,
)
from app.exceptions import AppError
from app.marketplace.access import ItemAuthorizer, can_install_item
from app.marketplace.install.agent_blueprint import (
    _install_agent_blueprint_item,
    _overwrite_agent_blueprint_installation,
)
from app.marketplace.install.common import (
    _existing_installation,
    _now,
    _resolve_version,
    load_install_source_item,
)
from app.marketplace.install.mcp import _install_mcp_item, _overwrite_mcp_installation
from app.marketplace.install.skill import (
    _install_skill_item,
    _overwrite_skill_installation,
    _refresh_skill_installation_bindings,
)
from app.marketplace.install.snapshot import _rmtree_skill_storage
from app.marketplace.install.source_gate import installable_source
from app.marketplace.install_locks import lock_marketplace_item_install
from app.marketplace.schemas import (
    InstallMarketplaceItemIn,
    UpdateMarketplaceInstallationIn,
)
from app.models.agent import Agent
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill
from app.storage.paths import resolve_company_data_path

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


async def install_item(
    db: AsyncSession,
    *,
    item_id: uuid.UUID,
    user: CurrentUser,
    context: OrgContext,
    body: InstallMarketplaceItemIn,
    authorize: ItemAuthorizer | None = None,
) -> MarketplaceInstallation:
    """Install (or re-use) a marketplace item for ``user``. Caller must
    commit on success — we don't double-commit here because routers wrap
    install + side effects in one unit of work.
    """

    if context.user_id != user.id:
        raise marketplace_item_not_found()
    try:
        await resolve_private_actor(
            db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
        )
    except AppError:
        raise marketplace_item_not_found() from None
    # Bezos OPEN-1 (2026-05-19): ``can_install_item`` walks
    # ``item.acl_entries`` for restricted-visibility items. Plain
    # ``db.get(MarketplaceItem, ...)`` returns the row without
    # eager-loading relationships, and the subsequent lazy access fires
    # an unsupported sync IO under the async session →
    # ``MissingGreenlet`` → 500. We surface 500 instead of the intended
    # 404, which doubles as an **enumeration oracle** (500 only happens
    # for restricted items the caller can't see).
    # Fix: eager-load ``acl_entries`` so the permission check stays in
    # pre-loaded memory. Version payloads are loaded only by the item-bound
    # query in ``_resolve_version`` after the item permission check.
    if authorize is not None and authorize.enforced:
        item = await authorize(item_id, None)  # D-05: OpenFGA can_install
    else:
        item = await load_install_source_item(db, item_id, context)
        if authorize is not None:
            item = await authorize(item_id, item)  # shadow: legacy answer + sample
        elif not can_install_item(item, user):
            # Collapse forbidden + missing for enumeration safety.
            logger.info(
                "marketplace_install_forbidden user=%s item=%s",
                user.id,
                item_id,
            )
            raise marketplace_item_not_found()

    version = await _resolve_version(db, item=item, version_id=body.version_id)
    await lock_marketplace_item_install(db, item_id=item.id)

    if item.resource_type == "mcp":
        return await _install_mcp_item(
            db,
            item=item,
            version=version,
            user=user,
            body=body,
            context=context,
        )

    if item.resource_type == "agent":
        return await _install_agent_blueprint_item(
            db,
            item=item,
            version=version,
            user=user,
            body=body,
            context=context,
        )

    # Resource_type guard: known marketplace resource types are handled above.
    if item.resource_type != "skill":
        logger.info("marketplace_install_unsupported_resource_type %s", item.resource_type)
        raise marketplace_item_not_found()

    # install_mode dispatch (Spec §10.8 / desc 단계 3)
    existing = await _existing_installation(db, item=item, user=user, context=context)
    if existing is not None:
        if body.install_mode == "reuse_or_update":
            if existing.installed_skill_id is None:
                raise marketplace_item_not_found()
            skill = await db.scalar(
                private_scoped(
                    select(Skill).where(Skill.id == existing.installed_skill_id), Skill, context
                )
            )
            if skill is None:
                raise marketplace_item_not_found()
            return await _refresh_skill_installation_bindings(
                db,
                installation=existing,
                skill=skill,
                user=user,
                body=body,
                context=context,
            )
        if body.install_mode == "overwrite_existing":
            # Delete the existing skill + installation, then re-install.
            await _remove_install_artifacts(db, existing, context)
            await db.flush()
        # ``new_copy`` falls through to create a fresh row.

    return await _install_skill_item(
        db,
        item=item,
        version=version,
        user=user,
        body=body,
        context=context,
    )


# ---------------------------------------------------------------------------
# Update (Spec §10.3)
# ---------------------------------------------------------------------------


async def update_installation(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    user: CurrentUser,
    context: OrgContext,
    body: UpdateMarketplaceInstallationIn,
    authorize: ItemAuthorizer | None = None,
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

    if context.user_id != user.id:
        raise marketplace_item_not_found()
    installation = await load_private_installation(db, installation_id, context)
    if installation is None or installation.user_id != user.id or installation.item_id is None:
        raise marketplace_item_not_found()  # source_removed (I-07): nothing to update from

    item = await installable_source(db, installation.item_id, context, user, authorize)
    if item.latest_version_id is None:
        raise marketplace_item_not_found()

    latest = await _resolve_version(db, item=item, version_id=None)

    # Refuse silent overwrites when the user has edited the installed
    # copy — they must opt in explicitly with overwrite / install_new_copy.
    skill = (
        await db.scalar(
            private_scoped(
                select(Skill).where(Skill.id == installation.installed_skill_id), Skill, context
            )
        )
        if installation.installed_skill_id is not None
        else None
    )
    blueprint = (
        await db.scalar(
            private_scoped(
                select(AgentBlueprint).where(
                    AgentBlueprint.id == installation.installed_agent_blueprint_id
                ),
                AgentBlueprint,
                context,
            )
        )
        if installation.installed_agent_blueprint_id is not None
        else None
    )
    dirty = bool(
        installation.is_dirty or (skill and skill.is_dirty) or (blueprint and blueprint.is_dirty)
    )
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
        return await install_item(
            db,
            item_id=item.id,
            user=user,
            body=InstallMarketplaceItemIn(
                version_id=latest.id,
                install_mode="new_copy",
                install_missing_credentials="needs_setup",
            ),
            context=context,
        )

    if installation.resource_type == "mcp":
        return await _overwrite_mcp_installation(
            db,
            installation=installation,
            item=item,
            latest=latest,
            user=user,
            context=context,
        )

    if installation.resource_type == "agent":
        return await _overwrite_agent_blueprint_installation(
            db,
            installation=installation,
            item=item,
            latest=latest,
            user=user,
            context=context,
        )

    # ``overwrite`` — replace files in place so agent_skills rows and
    # user-specific skill references keep the same skill_id.
    if installation.resource_type != "skill":
        raise marketplace_item_not_found()
    if skill is None:
        raise marketplace_item_not_found()
    return await _overwrite_skill_installation(
        db,
        installation=installation,
        item=item,
        latest=latest,
        user=user,
        skill=skill,
        context=context,
    )


# ---------------------------------------------------------------------------
# Delete (Spec §3.11)
# ---------------------------------------------------------------------------


async def delete_installation(
    db: AsyncSession,
    *,
    installation_id: uuid.UUID,
    user: CurrentUser,
    context: OrgContext,
    delete_resource: bool = False,
) -> None:
    """Soft delete by default (Spec §3.11 — link suspension keeps the
    user's skill row intact). ``delete_resource=True`` cascades into the
    installed skill (filesystem + DB row).
    """

    installation = await load_private_installation(db, installation_id, context)
    if installation is None or installation.user_id != user.id:
        raise marketplace_item_not_found()

    if delete_resource:
        await _remove_install_artifacts(db, installation, context)
        await db.delete(installation)
        return

    installation.install_status = "uninstalled"
    installation.updated_at = _now()
    # Keep the blueprint row (mirror skill soft-delete — accepted orphan
    # trade-off) but sync its status so the list/detail endpoints don't
    # surface it as a stale ``active`` ghost after re-install.
    if installation.installed_agent_blueprint_id is not None:
        blueprint = await db.scalar(
            private_scoped(
                select(AgentBlueprint).where(
                    AgentBlueprint.id == installation.installed_agent_blueprint_id
                ),
                AgentBlueprint,
                context,
            )
        )
        if blueprint is not None:
            blueprint.install_status = "uninstalled"
            blueprint.updated_at = _now()


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


async def _remove_install_artifacts(
    db: AsyncSession,
    installation: MarketplaceInstallation,
    context: OrgContext,
) -> None:
    """Remove the installed skill row + its on-disk directory. Used by
    overwrite/uninstall paths.

    The persisted installation and every private child must match the supplied context.
    """

    scoped_installation = await load_private_installation(db, installation.id, context)
    if scoped_installation is None:
        raise marketplace_item_not_found()
    installation = scoped_installation

    if installation.installed_skill_id is not None:
        skill = await db.scalar(
            private_scoped(
                select(Skill).where(Skill.id == installation.installed_skill_id), Skill, context
            )
        )
        if skill is not None:
            if skill.storage_path:
                # text-kind skills store SKILL.md path — climb one level
                # before delete; package-kind storage_path is the dir.
                # is_file()/exists() go through ``to_thread`` to stay
                # ASYNC240-clean.
                await asyncio.to_thread(
                    _rmtree_skill_storage,
                    resolve_company_data_path(skill.storage_path, skill.tenant_id),
                )
            await db.delete(skill)
    if installation.installed_mcp_server_id is not None:
        server = await db.scalar(
            private_scoped(
                select(McpServer).where(McpServer.id == installation.installed_mcp_server_id),
                McpServer,
                context,
            )
        )
        if server is not None:
            tools = (
                (await db.execute(select(McpTool).where(McpTool.server_id == server.id)))
                .scalars()
                .all()
            )
            for tool in tools:
                await db.delete(tool)
            await db.delete(server)
    if installation.installed_agent_blueprint_id is not None:
        blueprint = await db.scalar(
            private_scoped(
                select(AgentBlueprint).where(
                    AgentBlueprint.id == installation.installed_agent_blueprint_id
                ),
                AgentBlueprint,
                context,
            )
        )
        if blueprint is not None:
            await db.delete(blueprint)
    if installation.installed_agent_id is not None:
        agent = await db.scalar(
            private_scoped(
                select(Agent).where(Agent.id == installation.installed_agent_id), Agent, context
            )
        )
        if agent is not None:
            await db.delete(agent)
    await db.delete(installation)


__all__: list[str] = [
    "delete_installation",
    "install_item",
    "update_installation",
]


# Silence Any-unused lint when generics aren't referenced.
_ANY_HINT: Any = None
