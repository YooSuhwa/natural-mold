"""Origin / publication / installation summary derivation.

Maps DB state → ``ResourceOriginSummaryOut`` / ``ResourcePublicationSummaryOut``
/ ``MarketplaceInstallationSummary`` / ``CredentialSummaryOut``.

The functions in this module are pure projections — they take ORM rows
(possibly preloaded) and return Pydantic responses. The only DB access
is ``bulk_derive_*`` helpers that issue ONE round-trip per resource type
to load ``publication_links`` + ``installations`` + ACL counts in a single
query (anti-N+1 — per progress.txt gotchas).

Used by:
* ``marketplace.service.list_items`` / ``get_item``
* ``routers.skills`` (skill detail + list responses)
* ``routers.mcp_servers`` / ``routers.agents`` (publication only, Phase 1)
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.marketplace.schemas import (
    CredentialSummaryOut,
    MarketplaceInstallationSummary,
    ResourceOriginSummaryOut,
    ResourcePublicationSummaryOut,
)
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceItemACL,
    MarketplacePublicationLink,
    MarketplaceVersion,
)

if TYPE_CHECKING:
    from app.dependencies import CurrentUser
    from app.models.agent_blueprint import AgentBlueprint
    from app.models.credential import Credential
    from app.models.mcp_server import McpServer
    from app.models.skill import Skill


# ---------------------------------------------------------------------------
# Labels for ResourceOriginSummaryOut
# ---------------------------------------------------------------------------

_ORIGIN_LABELS: dict[str, str] = {
    "created_by_me": "직접 만든 리소스",
    "imported_by_me": "내가 가져온 리소스",
    "built_in_k_skill": "기본 제공 (k-skill)",
    "shared_with_me": "공유받은 리소스",
    "community": "커뮤니티",
    "system_seed": "기본 제공",
}


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------


def derive_origin_summary_for_skill(skill: Skill, user: CurrentUser) -> ResourceOriginSummaryOut:
    """Decide origin ``kind`` from skill lineage + current user identity.

    Priority order (Spec §7.5 + module-contracts.md §3.2):

    1. ``is_system + source_kind='k-skill'``     → ``built_in_k_skill``
    2. ``is_system + source_kind='system_seed'`` → ``system_seed``
    3. ``origin_user_id != current_user`` *and* the source item is restricted
        → ``shared_with_me``
    4. ``origin_user_id != current_user`` *and* the source item is public
        → ``community``
    5. ``origin_user_id == current_user`` *and* source_marketplace_item_id set
        → ``imported_by_me``
    6. Default → ``created_by_me`` (e.g. ``source_marketplace_item_id IS NULL``).

    The router caller decides whether to show this — list responses may
    return ``None`` for performance; detail responses always populate.
    """

    kind: str
    if skill.is_system and skill.source_kind == "k-skill":
        kind = "built_in_k_skill"
    elif skill.is_system and skill.source_kind == "system_seed":
        kind = "system_seed"
    elif (
        skill.origin_user_id is not None
        and skill.origin_user_id != user.id
        and skill.source_kind in ("user", "import")
    ):
        # Without a join on the source item we conservatively report
        # ``shared_with_me``; the marketplace catalog already gates by
        # visibility, so leaks here are limited to label text.
        kind = "shared_with_me"
    elif skill.origin_user_id == user.id and skill.source_marketplace_item_id is not None:
        kind = "imported_by_me"
    else:
        kind = "created_by_me"

    return ResourceOriginSummaryOut(
        kind=kind,  # type: ignore[arg-type]
        label=_ORIGIN_LABELS[kind],
        source_name=None,
        source_user_id=skill.origin_user_id,
        marketplace_item_id=skill.origin_marketplace_item_id,
        marketplace_version_id=skill.origin_marketplace_version_id,
    )


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def _derive_publication_state(
    item: MarketplaceItem | None,
) -> str:
    """Decision table (Spec §10.8 / contracts §3.3)."""

    if item is None:
        return "not_published"
    if item.status == "draft":
        return "draft"
    if item.status == "disabled":
        return "disabled"
    if item.status == "published":
        if item.visibility == "private":
            return "published_private"
        if item.visibility == "restricted":
            return "published_restricted"
        if item.visibility == "public":
            return "published_public_listed" if item.is_listed else "published_public_unlisted"
        if item.visibility == "unlisted":
            return "published_unlisted"
    # Deprecated etc. fall back to draft semantics for the summary.
    return "draft"


async def derive_publication_summary_for_skill(
    db: AsyncSession, skill: Skill
) -> ResourcePublicationSummaryOut:
    """Look up the user's publication link for this skill (best-effort)."""

    stmt = (
        select(MarketplacePublicationLink)
        .where(MarketplacePublicationLink.source_skill_id == skill.id)
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return ResourcePublicationSummaryOut(state="not_published")

    item = await db.get(MarketplaceItem, row.item_id)
    if item is None:
        return ResourcePublicationSummaryOut(state="not_published")

    latest_version: MarketplaceVersion | None = None
    if item.latest_version_id is not None:
        latest_version = await db.get(MarketplaceVersion, item.latest_version_id)

    acl_count = (
        await db.execute(
            select(func.count())
            .select_from(MarketplaceItemACL)
            .where(MarketplaceItemACL.item_id == item.id)
        )
    ).scalar_one()

    return ResourcePublicationSummaryOut(
        state=_derive_publication_state(item),  # type: ignore[arg-type]
        item_id=item.id,
        visibility=item.visibility,  # type: ignore[arg-type]
        status=item.status,  # type: ignore[arg-type]
        is_listed=item.is_listed,
        latest_version_id=latest_version.id if latest_version else None,
        version_number=latest_version.version_number if latest_version else None,
        shared_user_count=int(acl_count or 0),
    )


async def bulk_derive_publication_summaries_for_skills(
    db: AsyncSession,
    skills: Iterable[Skill],
) -> dict[uuid.UUID, ResourcePublicationSummaryOut]:
    """Bulk publication summaries keyed by skill id."""

    skill_ids = [skill.id for skill in skills]
    if not skill_ids:
        return {}

    links = (
        (
            await db.execute(
                select(MarketplacePublicationLink).where(
                    MarketplacePublicationLink.source_skill_id.in_(skill_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    by_skill = {link.source_skill_id: link for link in links if link.source_skill_id}
    item_ids = [link.item_id for link in links]
    items_by_id: dict[uuid.UUID, MarketplaceItem] = {}
    if item_ids:
        items = (
            (
                await db.execute(
                    select(MarketplaceItem)
                    .where(MarketplaceItem.id.in_(item_ids))
                    .options(selectinload(MarketplaceItem.latest_version))
                )
            )
            .scalars()
            .all()
        )
        items_by_id = {item.id: item for item in items}

    acl_counts: dict[uuid.UUID, int] = {}
    if item_ids:
        rows = (
            await db.execute(
                select(MarketplaceItemACL.item_id, func.count(MarketplaceItemACL.user_id))
                .where(MarketplaceItemACL.item_id.in_(item_ids))
                .group_by(MarketplaceItemACL.item_id)
            )
        ).all()
        acl_counts = {item_id: int(count or 0) for item_id, count in rows}

    summaries: dict[uuid.UUID, ResourcePublicationSummaryOut] = {}
    for skill_id in skill_ids:
        link = by_skill.get(skill_id)
        item = items_by_id.get(link.item_id) if link else None
        if item is None:
            summaries[skill_id] = ResourcePublicationSummaryOut(state="not_published")
            continue
        latest = item.latest_version
        summaries[skill_id] = ResourcePublicationSummaryOut(
            state=_derive_publication_state(item),  # type: ignore[arg-type]
            item_id=item.id,
            visibility=item.visibility,  # type: ignore[arg-type]
            status=item.status,  # type: ignore[arg-type]
            is_listed=item.is_listed,
            latest_version_id=latest.id if latest else None,
            version_number=latest.version_number if latest else None,
            shared_user_count=acl_counts.get(item.id, 0),
        )
    return summaries


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


async def derive_installation_summary(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    user_id: uuid.UUID,
) -> MarketplaceInstallationSummary:
    """Find the user's installation for this item (single row, best-effort)."""

    stmt = (
        select(MarketplaceInstallation)
        .where(
            MarketplaceInstallation.item_id == item.id,
            MarketplaceInstallation.user_id == user_id,
        )
        .limit(1)
    )
    installation = (await db.execute(stmt)).scalar_one_or_none()
    if installation is None:
        return MarketplaceInstallationSummary(installed=False)

    installed_resource_id: uuid.UUID | None = None
    if installation.resource_type == "skill":
        installed_resource_id = installation.installed_skill_id
    elif installation.resource_type == "agent":
        installed_resource_id = (
            installation.installed_agent_blueprint_id or installation.installed_agent_id
        )
    elif installation.resource_type == "mcp":
        installed_resource_id = installation.installed_mcp_server_id

    update_available = (
        item.latest_version_id is not None and installation.version_id != item.latest_version_id
    )

    dirty = bool(installation.is_dirty)
    # ``status`` from the DB row is authoritative for disabled/uninstalled;
    # we may upgrade ``active`` → ``needs_setup`` based on credential gaps.
    status = installation.install_status
    if installation.installed_skill_id is not None:
        from app.marketplace.credential_requirements import missing_required_keys
        from app.models.skill import Skill

        skill = await db.get(Skill, installation.installed_skill_id)
        if skill is not None and skill.is_dirty:
            dirty = True
        if skill is not None and status == "active":
            # Only flip if currently active — disabled/uninstalled retain
            # their explicit state. Spec §10.5: active + missing required
            # bindings is the canonical ``needs_setup`` trigger.
            class _UserLike:  # local stub — missing_required_keys only reads .id
                def __init__(self, uid: uuid.UUID) -> None:
                    self.id = uid

            missing = await missing_required_keys(
                db,
                skill=skill,
                user=_UserLike(user_id),  # type: ignore[arg-type]
            )
            if missing:
                status = "needs_setup"

    status, dirty = await _recompute_installation_projection_state(
        db,
        installation=installation,
        user_id=user_id,
        status=status,
        dirty=dirty,
    )

    return MarketplaceInstallationSummary(
        installed=installation.install_status != "uninstalled",
        installation_id=installation.id,
        installed_resource_id=installed_resource_id,
        status=status,  # type: ignore[arg-type]
        update_available=update_available,
        dirty=dirty,
    )


def _installation_resource_id(installation: MarketplaceInstallation) -> uuid.UUID | None:
    if installation.resource_type == "skill":
        return installation.installed_skill_id
    if installation.resource_type == "agent":
        return installation.installed_agent_blueprint_id or installation.installed_agent_id
    if installation.resource_type == "mcp":
        return installation.installed_mcp_server_id
    return None


@dataclass
class _InstallationPrefetch:
    """Bulk-loaded rows for ``bulk_derive_installation_summaries``.

    Avoids per-installation ``db.get`` round trips (anti-N+1) — the bulk
    caller loads versions/servers/blueprints/credentials with IN queries
    once and the projection helpers read from these maps instead.
    """

    versions: dict[uuid.UUID, MarketplaceVersion] = field(default_factory=dict)
    servers: dict[uuid.UUID, McpServer] = field(default_factory=dict)
    blueprints: dict[uuid.UUID, AgentBlueprint] = field(default_factory=dict)
    credentials: dict[uuid.UUID, Credential] = field(default_factory=dict)


def _required_credential_requirements(
    version: MarketplaceVersion | None,
) -> list[dict[str, Any]]:
    if version is None:
        return []
    return [
        requirement
        for requirement in (version.credential_requirements or [])
        if isinstance(requirement, dict) and requirement.get("required") and requirement.get("key")
    ]


async def _credential_binding_missing(
    db: AsyncSession,
    *,
    credential_id: uuid.UUID | str | None,
    user_id: uuid.UUID,
    requirement: dict[str, Any],
    prefetch: _InstallationPrefetch | None = None,
) -> bool:
    if credential_id is None:
        return True
    try:
        normalized_id = uuid.UUID(str(credential_id))
    except (TypeError, ValueError):
        return True

    if prefetch is not None:
        credential = prefetch.credentials.get(normalized_id)
    else:
        from app.models.credential import Credential

        credential = await db.get(Credential, normalized_id)
    if credential is None or credential.user_id != user_id:
        return True
    expected_definition = requirement.get("definition_key")
    return bool(expected_definition and credential.definition_key != expected_definition)


async def _mcp_installation_missing_required_credentials(
    db: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    user_id: uuid.UUID,
    prefetch: _InstallationPrefetch | None = None,
) -> bool:
    if installation.installed_mcp_server_id is None:
        return False

    if prefetch is not None:
        version = prefetch.versions.get(installation.version_id)
    else:
        version = await db.get(MarketplaceVersion, installation.version_id)
    requirements = _required_credential_requirements(version)
    if not requirements:
        return False

    if prefetch is not None:
        server = prefetch.servers.get(installation.installed_mcp_server_id)
    else:
        from app.models.mcp_server import McpServer

        server = await db.get(McpServer, installation.installed_mcp_server_id)
    if server is None:
        return True

    for requirement in requirements:
        key = str(requirement.get("key"))
        credential_id = server.credential_id if key == "mcp_auth" else None
        if await _credential_binding_missing(
            db,
            credential_id=credential_id,
            user_id=user_id,
            requirement=requirement,
            prefetch=prefetch,
        ):
            return True
    return False


async def _agent_blueprint_installation_state(
    db: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    user_id: uuid.UUID,
    prefetch: _InstallationPrefetch | None = None,
) -> tuple[bool, bool]:
    if installation.installed_agent_blueprint_id is None:
        return False, False

    if prefetch is not None:
        blueprint = prefetch.blueprints.get(installation.installed_agent_blueprint_id)
    else:
        from app.models.agent_blueprint import AgentBlueprint

        blueprint = await db.get(AgentBlueprint, installation.installed_agent_blueprint_id)
    if blueprint is None:
        return True, False

    if prefetch is not None:
        version = prefetch.versions.get(installation.version_id)
    else:
        version = await db.get(MarketplaceVersion, installation.version_id)
    bindings = blueprint.credential_bindings or {}
    for requirement in _required_credential_requirements(version):
        credential_id = bindings.get(str(requirement.get("key")))
        if await _credential_binding_missing(
            db,
            credential_id=credential_id,
            user_id=user_id,
            requirement=requirement,
            prefetch=prefetch,
        ):
            return True, bool(blueprint.is_dirty)
    return False, bool(blueprint.is_dirty)


async def _recompute_installation_projection_state(
    db: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    user_id: uuid.UUID,
    status: str,
    dirty: bool,
    prefetch: _InstallationPrefetch | None = None,
) -> tuple[str, bool]:
    if status != "active":
        return status, dirty

    if installation.installed_mcp_server_id is not None:
        missing = await _mcp_installation_missing_required_credentials(
            db,
            installation=installation,
            user_id=user_id,
            prefetch=prefetch,
        )
        return ("needs_setup" if missing else status), dirty

    if installation.installed_agent_blueprint_id is not None:
        missing, blueprint_dirty = await _agent_blueprint_installation_state(
            db,
            installation=installation,
            user_id=user_id,
            prefetch=prefetch,
        )
        dirty = bool(dirty or blueprint_dirty)
        return ("needs_setup" if missing else status), dirty

    return status, dirty


async def _load_installation_prefetch(
    db: AsyncSession,
    installations: Iterable[MarketplaceInstallation],
) -> _InstallationPrefetch:
    """Load version/server/blueprint/credential rows for the recompute
    helpers with one IN query per table (anti-N+1)."""

    from app.models.agent_blueprint import AgentBlueprint
    from app.models.credential import Credential
    from app.models.mcp_server import McpServer

    version_ids: set[uuid.UUID] = set()
    server_ids: set[uuid.UUID] = set()
    blueprint_ids: set[uuid.UUID] = set()
    for installation in installations:
        if installation.installed_mcp_server_id is not None:
            server_ids.add(installation.installed_mcp_server_id)
            version_ids.add(installation.version_id)
        if installation.installed_agent_blueprint_id is not None:
            blueprint_ids.add(installation.installed_agent_blueprint_id)
            version_ids.add(installation.version_id)

    prefetch = _InstallationPrefetch()
    if version_ids:
        rows = (
            (
                await db.execute(
                    select(MarketplaceVersion).where(MarketplaceVersion.id.in_(version_ids))
                )
            )
            .scalars()
            .all()
        )
        prefetch.versions = {row.id: row for row in rows}
    if server_ids:
        rows = (
            (await db.execute(select(McpServer).where(McpServer.id.in_(server_ids))))
            .scalars()
            .all()
        )
        prefetch.servers = {row.id: row for row in rows}
    if blueprint_ids:
        rows = (
            (await db.execute(select(AgentBlueprint).where(AgentBlueprint.id.in_(blueprint_ids))))
            .scalars()
            .all()
        )
        prefetch.blueprints = {row.id: row for row in rows}

    credential_ids: set[uuid.UUID] = set()
    for server in prefetch.servers.values():
        if server.credential_id is not None:
            credential_ids.add(server.credential_id)
    for blueprint in prefetch.blueprints.values():
        for raw_id in (blueprint.credential_bindings or {}).values():
            try:
                credential_ids.add(uuid.UUID(str(raw_id)))
            except (TypeError, ValueError):
                continue
    if credential_ids:
        rows = (
            (await db.execute(select(Credential).where(Credential.id.in_(credential_ids))))
            .scalars()
            .all()
        )
        prefetch.credentials = {row.id: row for row in rows}
    return prefetch


async def bulk_derive_installation_summaries(
    db: AsyncSession,
    *,
    items: Iterable[MarketplaceItem],
    user_id: uuid.UUID,
) -> dict[uuid.UUID, MarketplaceInstallationSummary]:
    """Bulk installation summaries keyed by marketplace item id."""

    items_by_id = {item.id: item for item in items}
    if not items_by_id:
        return {}
    installations = (
        (
            await db.execute(
                select(MarketplaceInstallation).where(
                    MarketplaceInstallation.item_id.in_(items_by_id),
                    MarketplaceInstallation.user_id == user_id,
                )
            )
        )
        .scalars()
        .all()
    )

    skill_ids = [
        installation.installed_skill_id
        for installation in installations
        if installation.installed_skill_id is not None
    ]
    skills_by_id: dict[uuid.UUID, Skill] = {}
    if skill_ids:
        from app.models.skill import Skill

        skills = (await db.execute(select(Skill).where(Skill.id.in_(skill_ids)))).scalars().all()
        skills_by_id = {skill.id: skill for skill in skills}

    prefetch = await _load_installation_prefetch(db, installations)

    summaries = {
        item_id: MarketplaceInstallationSummary(installed=False) for item_id in items_by_id
    }
    for installation in installations:
        item = items_by_id[installation.item_id]
        dirty = bool(installation.is_dirty)
        status = installation.install_status
        if installation.installed_skill_id is not None:
            skill = skills_by_id.get(installation.installed_skill_id)
            if skill is not None and skill.is_dirty:
                dirty = True
            if skill is not None and status == "active":
                from app.marketplace.credential_requirements import missing_required_keys

                class _UserLike:
                    def __init__(self, uid: uuid.UUID) -> None:
                        self.id = uid

                missing = await missing_required_keys(
                    db,
                    skill=skill,
                    user=_UserLike(user_id),  # type: ignore[arg-type]
                )
                if missing:
                    status = "needs_setup"

        status, dirty = await _recompute_installation_projection_state(
            db,
            installation=installation,
            user_id=user_id,
            status=status,
            dirty=dirty,
            prefetch=prefetch,
        )

        summaries[item.id] = MarketplaceInstallationSummary(
            installed=installation.install_status != "uninstalled",
            installation_id=installation.id,
            installed_resource_id=_installation_resource_id(installation),
            status=status,  # type: ignore[arg-type]
            update_available=(
                item.latest_version_id is not None
                and installation.version_id != item.latest_version_id
            ),
            dirty=dirty,
        )
    return summaries


async def bulk_derive_skill_installation_summaries(
    db: AsyncSession,
    skills: Iterable[Skill],
) -> dict[uuid.UUID, MarketplaceInstallationSummary | None]:
    """Bulk skill installation summaries keyed by installed skill id."""

    skills_by_id = {skill.id: skill for skill in skills}
    if not skills_by_id:
        return {}
    rows = (
        (
            await db.execute(
                select(MarketplaceInstallation).where(
                    MarketplaceInstallation.installed_skill_id.in_(skills_by_id)
                )
            )
        )
        .scalars()
        .all()
    )
    summaries: dict[uuid.UUID, MarketplaceInstallationSummary | None] = dict.fromkeys(skills_by_id)
    for row in rows:
        if row.installed_skill_id is None:
            continue
        skill = skills_by_id[row.installed_skill_id]
        summaries[row.installed_skill_id] = MarketplaceInstallationSummary(
            installed=row.install_status != "uninstalled",
            installation_id=row.id,
            installed_resource_id=row.installed_skill_id,
            status=row.install_status,  # type: ignore[arg-type]
            update_available=False,
            dirty=bool(row.is_dirty or skill.is_dirty),
        )
    return summaries


# ---------------------------------------------------------------------------
# Credential summary (server hint for "needs setup")
# ---------------------------------------------------------------------------


def derive_credential_summary(
    requirements: Iterable[dict[str, Any]] | None,
    *,
    missing_required_count: int = 0,
) -> CredentialSummaryOut:
    """Compress a credential_requirements list to UI-friendly counters.

    ``missing_required_count`` is supplied by the caller because computing
    it requires knowing the user's bindings — kept out of this leaf so
    the function stays free of DB calls.
    """

    required = 0
    optional = 0
    hosted_proxy = False
    manual_login = False
    for req in requirements or []:
        scope = req.get("scope") or "user"
        if scope == "system_dependency":
            hosted_proxy = True
            continue
        if scope == "manual":
            manual_login = True
            continue
        if req.get("required", True):
            required += 1
        else:
            optional += 1

    status: str
    if hosted_proxy and required == 0 and optional == 0:
        status = "hosted_proxy"
    elif manual_login and required == 0 and optional == 0:
        status = "manual_login"
    elif required > 0:
        status = "required"
    elif optional > 0:
        status = "optional"
    else:
        status = "none"

    return CredentialSummaryOut(
        status=status,  # type: ignore[arg-type]
        required_count=required,
        optional_count=optional,
        missing_required_count=missing_required_count,
    )


async def mark_installation_dirty(db: AsyncSession, *, installed_skill_id: uuid.UUID) -> None:
    """Flag an installation as dirty after content edit.

    Best-effort — no error if the resource isn't a marketplace install.
    Caller commits.
    """

    stmt = select(MarketplaceInstallation).where(
        MarketplaceInstallation.installed_skill_id == installed_skill_id
    )
    for row in (await db.execute(stmt)).scalars():
        row.is_dirty = True


__all__ = [
    "bulk_derive_installation_summaries",
    "bulk_derive_publication_summaries_for_skills",
    "bulk_derive_skill_installation_summaries",
    "derive_credential_summary",
    "derive_installation_summary",
    "derive_origin_summary_for_skill",
    "derive_publication_summary_for_skill",
    "mark_installation_dirty",
]
