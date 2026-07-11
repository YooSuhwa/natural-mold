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
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
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
from app.marketplace.install_locks import lock_marketplace_item_install
from app.marketplace.payloads import canonical_json_hash
from app.marketplace.schemas import (
    InstallMarketplaceItemIn,
    UpdateMarketplaceInstallationIn,
)
from app.models.agent import Agent
from app.models.agent_blueprint import AgentBlueprint
from app.models.credential import Credential
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceVersion,
    SkillCredentialBinding,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
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


def _derive_origin(item: MarketplaceItem, user: CurrentUser) -> tuple[str, uuid.UUID | None]:
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


async def _replace_skill_snapshot(version: MarketplaceVersion, skill: Skill) -> None:
    """Replace an installed skill's on-disk bytes while preserving its id."""

    target = _target_for(skill.id)
    tmp = target.with_suffix(".update.tmp")
    if tmp.exists():
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)

    await _copy_snapshot(version, tmp)
    try:
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        await asyncio.to_thread(tmp.rename, target)
    except Exception:
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)
        raise


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


def _mcp_required_keys(version: MarketplaceVersion) -> list[str]:
    requirements = version.credential_requirements or []
    keys: list[str] = []
    for requirement in requirements:
        if isinstance(requirement, dict) and requirement.get("required"):
            key = requirement.get("key")
            if isinstance(key, str) and key:
                keys.append(key)
    return keys


def _credential_requirements_by_key(
    version: MarketplaceVersion,
) -> dict[str, dict[str, Any]]:
    return {
        str(req.get("key")): req
        for req in (version.credential_requirements or [])
        if isinstance(req, dict) and req.get("key")
    }


def _required_credential_keys(version: MarketplaceVersion) -> list[str]:
    return [
        key
        for key, requirement in _credential_requirements_by_key(version).items()
        if requirement.get("required")
    ]


def _agent_blueprint_payload_with_requirements(
    version: MarketplaceVersion,
) -> dict[str, Any]:
    payload = dict(version.payload or {})
    if version.credential_requirements:
        setup = payload.get("setup") if isinstance(payload.get("setup"), dict) else {}
        payload["setup"] = {
            **setup,
            "required_credentials": list(version.credential_requirements or []),
        }
    return payload


async def _validate_version_credential_bindings(
    db: AsyncSession,
    *,
    version: MarketplaceVersion,
    user: CurrentUser,
    bindings: dict[str, uuid.UUID],
) -> dict[str, str]:
    requirement_by_key = _credential_requirements_by_key(version)
    normalized: dict[str, str] = {}
    for key, credential_id in bindings.items():
        requirement = requirement_by_key.get(key)
        if requirement is None:
            raise marketplace_credential_required(f"unknown credential binding: {key}")

        credential = await db.get(Credential, credential_id)
        if credential is None or credential.user_id != user.id:
            raise marketplace_credential_required(f"invalid credential binding: {key}")

        expected_definition = requirement.get("definition_key")
        if expected_definition and credential.definition_key != expected_definition:
            raise marketplace_credential_required(f"credential definition mismatch: {key}")
        normalized[key] = str(credential.id)
    return normalized


async def _validate_mcp_bindings(
    db: AsyncSession,
    *,
    version: MarketplaceVersion,
    user: CurrentUser,
    bindings: dict[str, uuid.UUID],
) -> uuid.UUID | None:
    requirements = version.credential_requirements or []
    requirement_by_key = {
        str(req.get("key")): req for req in requirements if isinstance(req, dict) and req.get("key")
    }
    credential_id = bindings.get("mcp_auth")
    if credential_id is None:
        return None

    requirement = requirement_by_key.get("mcp_auth")
    credential = await db.get(Credential, credential_id)
    if credential is None or credential.user_id != user.id:
        raise marketplace_credential_required("MCP credential binding is invalid")
    expected_definition = (
        requirement.get("definition_key") if isinstance(requirement, dict) else None
    )
    if expected_definition and credential.definition_key != expected_definition:
        raise marketplace_credential_required("MCP credential definition mismatch")
    return credential.id


def _apply_mcp_payload_to_server(
    *,
    server: McpServer,
    payload: dict[str, Any],
    name: str,
    credential_id: uuid.UUID | None,
) -> None:
    server.name = name
    server.description = payload.get("description")
    server.transport = str(payload.get("transport") or "streamable_http")
    server.url = payload.get("url")
    server.command = payload.get("command")
    server.args = list(payload.get("args") or [])
    server.env_vars = dict(payload.get("env_vars") or {})
    server.headers = dict(payload.get("headers") or {})
    server.credential_id = credential_id
    server.status = "unknown"
    server.last_error = None


async def _materialize_mcp_tool_snapshot(
    db: AsyncSession,
    *,
    server: McpServer,
    payload: dict[str, Any],
    preserve_enabled: bool = False,
) -> None:
    """Materialize publisher-provided ``tool_snapshot`` rows as ``McpTool``.

    ``install_defaults.enabled_tool_names`` (written by the publish flow in
    ``mcp_server.build_mcp_server_payload``) selects which snapshot tools
    start enabled; tools outside the list are created/updated disabled.
    When the list is absent the legacy behavior (everything enabled) holds.

    ``preserve_enabled=True`` keeps the existing ``enabled`` flag on tools the
    user may have toggled manually — used by credential-only refreshes
    (``reuse_or_update``) where re-applying publish defaults would be
    surprising. New tools and snapshot pruning still follow the defaults.

    Known limitation (design §6.1): snapshot tools trust the publisher's
    ``name``/``description``/``input_schema`` verbatim — until a real MCP
    discovery run validates the server, phantom tools (entries the server
    never actually exposes) are possible. We intentionally do NOT run a
    network discovery here so installs stay deterministic and offline-safe;
    the scheduler/health-poll discovery path reconciles the truth later.
    """

    snapshot_present = isinstance(payload.get("tool_snapshot"), list)
    snapshot = payload.get("tool_snapshot") if snapshot_present else []
    install_defaults = (
        payload.get("install_defaults") if isinstance(payload.get("install_defaults"), dict) else {}
    )
    raw_enabled_names = install_defaults.get("enabled_tool_names")
    enabled_names: set[str] | None = (
        {str(name) for name in raw_enabled_names} if isinstance(raw_enabled_names, list) else None
    )
    valid_names: set[str] = set()
    for row in snapshot:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        valid_names.add(name)
        enabled = name in enabled_names if enabled_names is not None else True
        input_schema = row.get("input_schema") if isinstance(row.get("input_schema"), dict) else {}
        tool = (
            await db.execute(
                select(McpTool).where(McpTool.server_id == server.id, McpTool.name == name).limit(1)
            )
        ).scalar_one_or_none()
        if tool is None:
            tool = McpTool(
                id=uuid.uuid4(),
                server_id=server.id,
                name=name,
                description=row.get("description"),
                input_schema=input_schema,
                enabled=enabled,
                last_seen_at=_now(),
            )
            db.add(tool)
        else:
            tool.description = row.get("description")
            tool.input_schema = input_schema
            if not preserve_enabled:
                tool.enabled = enabled
            tool.last_seen_at = _now()

    if snapshot_present:
        existing_tools = (
            (await db.execute(select(McpTool).where(McpTool.server_id == server.id)))
            .scalars()
            .all()
        )
        # The snapshot is the authoritative tool list for this version, so a
        # tool that vanished was genuinely removed (e.g. renamed). Delete the
        # stale row instead of leaving a permanently-disabled dangling tool,
        # and drop any agent links that pointed at it (Postgres cascades on
        # the FK; the explicit delete keeps SQLite tests correct too).
        for tool in existing_tools:
            if tool.name not in valid_names:
                await db.execute(
                    delete(AgentMcpToolLink).where(AgentMcpToolLink.mcp_tool_id == tool.id)
                )
                await db.delete(tool)
        server.last_tool_count = len(valid_names)


async def _install_mcp_item(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    version: MarketplaceVersion,
    user: CurrentUser,
    body: InstallMarketplaceItemIn,
) -> MarketplaceInstallation:
    if version.payload_kind != "mcp_template":
        raise marketplace_invalid_package("version is not an MCP template")

    existing = await _existing_installation(db, item=item, user=user)
    if existing is not None and body.install_mode == "reuse_or_update":
        if not body.credential_bindings:
            return existing
        installed_version = await db.get(MarketplaceVersion, existing.version_id)
        binding_version = installed_version or version
        credential_id = await _validate_mcp_bindings(
            db,
            version=binding_version,
            user=user,
            bindings=body.credential_bindings,
        )
        missing = [
            key
            for key in _mcp_required_keys(binding_version)
            if key not in body.credential_bindings
        ]
        if missing and body.install_missing_credentials == "reject":
            raise marketplace_credential_required(
                f"missing required credential bindings: {', '.join(missing)}"
            )
        server_id = existing.installed_mcp_server_id
        if server_id is None:
            raise marketplace_invalid_package("installed MCP server is missing")
        server = await db.get(McpServer, server_id)
        # Re-validate ownership before mutating (mirror
        # ``_overwrite_mcp_installation``) — collapse to 404 per the
        # enumeration-safety convention.
        if server is None or server.user_id != user.id:
            raise marketplace_item_not_found()
        if credential_id is not None:
            server.credential_id = credential_id
        await _materialize_mcp_tool_snapshot(
            db,
            server=server,
            payload=binding_version.payload or {},
            preserve_enabled=True,
        )
        install_status = "needs_setup" if missing else "active"
        existing.install_status = install_status
        existing.is_dirty = False
        existing.updated_at = _now()
        await db.flush()
        return existing

    credential_id = await _validate_mcp_bindings(
        db,
        version=version,
        user=user,
        bindings=body.credential_bindings,
    )
    missing = [key for key in _mcp_required_keys(version) if key not in body.credential_bindings]
    if missing and body.install_missing_credentials == "reject":
        raise marketplace_credential_required(
            f"missing required credential bindings: {', '.join(missing)}"
        )
    install_status = "needs_setup" if missing else "active"

    payload = version.payload or {}
    name = body.name_override or payload.get("name") or item.name

    if (
        existing is not None
        and body.install_mode == "overwrite_existing"
        and existing.installed_mcp_server_id is not None
    ):
        server = await db.get(McpServer, existing.installed_mcp_server_id)
        # Re-validate ownership before mutating (mirror
        # ``_overwrite_mcp_installation``) — collapse to 404 per the
        # enumeration-safety convention.
        if server is None or server.user_id != user.id:
            raise marketplace_item_not_found()
        _apply_mcp_payload_to_server(
            server=server,
            payload=payload,
            name=str(name),
            credential_id=credential_id,
        )
        await _materialize_mcp_tool_snapshot(db, server=server, payload=payload)
        existing.version_id = version.id
        existing.install_status = install_status
        existing.is_dirty = False
        existing.updated_at = _now()
        await db.flush()
        return existing

    server = McpServer(
        id=uuid.uuid4(),
        user_id=user.id,
        name=str(name),
        description=payload.get("description"),
        transport=str(payload.get("transport") or "streamable_http"),
        url=payload.get("url"),
        command=payload.get("command"),
        args=list(payload.get("args") or []),
        env_vars=dict(payload.get("env_vars") or {}),
        headers=dict(payload.get("headers") or {}),
        credential_id=credential_id,
        status="unknown",
        is_system=False,
    )
    db.add(server)
    await db.flush()
    await _materialize_mcp_tool_snapshot(db, server=server, payload=payload)

    installation = MarketplaceInstallation(
        id=uuid.uuid4(),
        user_id=user.id,
        item_id=item.id,
        version_id=version.id,
        resource_type="mcp",
        installed_mcp_server_id=server.id,
        install_status=install_status,
        is_dirty=False,
        installed_at=_now(),
    )
    db.add(installation)
    await db.flush()
    return installation


async def _install_agent_blueprint_item(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    version: MarketplaceVersion,
    user: CurrentUser,
    body: InstallMarketplaceItemIn,
) -> MarketplaceInstallation:
    if version.payload_kind != "agent_spec":
        raise marketplace_invalid_package("version is not an Agent spec")

    existing = await _existing_installation(db, item=item, user=user)
    if existing is not None and body.install_mode == "reuse_or_update":
        if not body.credential_bindings:
            return existing
        blueprint_id = existing.installed_agent_blueprint_id
        if blueprint_id is None:
            raise marketplace_invalid_package("installed Agent Blueprint is missing")
        blueprint = await db.get(AgentBlueprint, blueprint_id)
        # Re-validate ownership before mutating (mirror
        # ``_overwrite_agent_blueprint_installation``) — collapse to 404
        # per the enumeration-safety convention.
        if blueprint is None or blueprint.user_id != user.id:
            raise marketplace_item_not_found()
        installed_version = await db.get(MarketplaceVersion, existing.version_id)
        binding_version = installed_version or version
        credential_bindings = await _validate_version_credential_bindings(
            db,
            version=binding_version,
            user=user,
            bindings=body.credential_bindings,
        )
        merged_bindings = {
            **(blueprint.credential_bindings or {}),
            **credential_bindings,
        }
        required = _required_credential_keys(binding_version)
        missing = [key for key in required if key not in merged_bindings]
        if missing and body.install_missing_credentials == "reject":
            raise marketplace_credential_required(
                f"missing required credential bindings: {', '.join(missing)}"
            )
        install_status = "needs_setup" if missing else "active"
        blueprint.credential_bindings = merged_bindings
        blueprint.install_status = install_status
        blueprint.is_dirty = False
        blueprint.updated_at = _now()
        existing.install_status = install_status
        existing.is_dirty = False
        existing.updated_at = _now()
        await db.flush()
        return existing

    credential_bindings = await _validate_version_credential_bindings(
        db,
        version=version,
        user=user,
        bindings=body.credential_bindings,
    )
    required = _required_credential_keys(version)
    missing = [key for key in required if key not in credential_bindings]
    if missing and body.install_missing_credentials == "reject":
        raise marketplace_credential_required(
            f"missing required credential bindings: {', '.join(missing)}"
        )
    install_status = "needs_setup" if missing else "active"

    payload = _agent_blueprint_payload_with_requirements(version)
    agent_spec = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    name = body.name_override or agent_spec.get("name") or payload.get("name") or item.name

    if (
        existing is not None
        and body.install_mode == "overwrite_existing"
        and existing.installed_agent_blueprint_id is not None
    ):
        blueprint = await db.get(AgentBlueprint, existing.installed_agent_blueprint_id)
        # Re-validate ownership before mutating (mirror
        # ``_overwrite_agent_blueprint_installation``) — collapse to 404
        # per the enumeration-safety convention.
        if blueprint is None or blueprint.user_id != user.id:
            raise marketplace_item_not_found()
        blueprint.name = str(name)
        blueprint.description = item.description
        blueprint.spec = payload
        blueprint.spec_hash = version.content_hash or canonical_json_hash(payload)
        blueprint.credential_bindings = credential_bindings
        blueprint.source_marketplace_item_id = item.id
        blueprint.source_marketplace_version_id = version.id
        blueprint.origin_user_id = item.owner_user_id
        blueprint.install_status = install_status
        blueprint.is_dirty = False
        blueprint.updated_at = _now()
        existing.version_id = version.id
        existing.install_status = install_status
        existing.is_dirty = False
        existing.updated_at = _now()
        await db.flush()
        return existing

    blueprint = AgentBlueprint(
        id=uuid.uuid4(),
        user_id=user.id,
        name=str(name),
        description=item.description,
        icon_id=item.icon_id,
        tags=list(item.tags or []),
        categories=list(item.categories or []),
        spec=payload,
        spec_hash=version.content_hash or canonical_json_hash(payload),
        credential_bindings=credential_bindings,
        source_marketplace_item_id=item.id,
        source_marketplace_version_id=version.id,
        origin_user_id=item.owner_user_id,
        origin_kind=_derive_origin(item, user)[0],
        install_status=install_status,
        is_dirty=False,
        created_agent_count=0,
    )
    db.add(blueprint)
    await db.flush()

    installation = MarketplaceInstallation(
        id=uuid.uuid4(),
        user_id=user.id,
        item_id=item.id,
        version_id=version.id,
        resource_type="agent",
        installed_agent_blueprint_id=blueprint.id,
        install_status=install_status,
        is_dirty=False,
        installed_at=_now(),
    )
    db.add(installation)
    await db.flush()
    return installation


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

    version = await _resolve_version(db, item=item, version_id=body.version_id)
    await lock_marketplace_item_install(db, item_id=item.id)

    if item.resource_type == "mcp":
        return await _install_mcp_item(
            db,
            item=item,
            version=version,
            user=user,
            body=body,
        )

    if item.resource_type == "agent":
        return await _install_agent_blueprint_item(
            db,
            item=item,
            version=version,
            user=user,
            body=body,
        )

    # Resource_type guard: known marketplace resource types are handled above.
    if item.resource_type != "skill":
        logger.info("marketplace_install_unsupported_resource_type %s", item.resource_type)
        raise marketplace_item_not_found()

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


# ---------------------------------------------------------------------------
# Update (Spec §10.3)
# ---------------------------------------------------------------------------


async def _mcp_install_status_for_server(
    db: AsyncSession,
    *,
    version: MarketplaceVersion,
    user: CurrentUser,
    server: McpServer,
) -> str:
    missing: list[str] = []
    requirement_by_key = _credential_requirements_by_key(version)
    for key in _required_credential_keys(version):
        requirement = requirement_by_key.get(key) or {}
        if key != "mcp_auth" or server.credential_id is None:
            missing.append(key)
            continue

        credential = await db.get(Credential, server.credential_id)
        expected_definition = requirement.get("definition_key")
        if (
            credential is None
            or credential.user_id != user.id
            or (expected_definition and credential.definition_key != expected_definition)
        ):
            missing.append(key)
    return "needs_setup" if missing else "active"


async def _overwrite_mcp_installation(
    db: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    item: MarketplaceItem,
    latest: MarketplaceVersion,
    user: CurrentUser,
) -> MarketplaceInstallation:
    if latest.payload_kind != "mcp_template":
        raise marketplace_invalid_package("latest version is not an MCP template")
    if installation.installed_mcp_server_id is None:
        raise marketplace_item_not_found()

    server = await db.get(McpServer, installation.installed_mcp_server_id)
    if server is None or server.user_id != user.id:
        raise marketplace_item_not_found()

    payload = latest.payload or {}
    name = payload.get("name") or item.name
    install_status = await _mcp_install_status_for_server(
        db,
        version=latest,
        user=user,
        server=server,
    )
    _apply_mcp_payload_to_server(
        server=server,
        payload=payload,
        name=str(name),
        credential_id=server.credential_id,
    )
    await _materialize_mcp_tool_snapshot(db, server=server, payload=payload)
    installation.version_id = latest.id
    installation.install_status = install_status
    installation.is_dirty = False
    installation.updated_at = _now()
    return installation


async def _agent_blueprint_status_from_bindings(
    db: AsyncSession,
    *,
    version: MarketplaceVersion,
    user: CurrentUser,
    stored_bindings: dict[str, Any] | None,
) -> tuple[str, dict[str, str]]:
    requirement_by_key = _credential_requirements_by_key(version)
    normalized: dict[str, str] = {}
    for key, raw_id in (stored_bindings or {}).items():
        requirement = requirement_by_key.get(str(key))
        if requirement is None:
            continue
        try:
            credential_id = uuid.UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        credential = await db.get(Credential, credential_id)
        expected_definition = requirement.get("definition_key")
        if (
            credential is None
            or credential.user_id != user.id
            or (expected_definition and credential.definition_key != expected_definition)
        ):
            continue
        normalized[str(key)] = str(credential.id)

    missing = [key for key in _required_credential_keys(version) if key not in normalized]
    return ("needs_setup" if missing else "active", normalized)


def _apply_agent_payload_to_blueprint(
    *,
    blueprint: AgentBlueprint,
    item: MarketplaceItem,
    version: MarketplaceVersion,
    name: str,
    payload: dict[str, Any],
    credential_bindings: dict[str, str],
    install_status: str,
) -> None:
    blueprint.name = name
    blueprint.description = item.description
    blueprint.icon_id = item.icon_id
    blueprint.tags = list(item.tags or [])
    blueprint.categories = list(item.categories or [])
    blueprint.spec = payload
    blueprint.spec_hash = version.content_hash or canonical_json_hash(payload)
    blueprint.credential_bindings = credential_bindings
    blueprint.source_marketplace_item_id = item.id
    blueprint.source_marketplace_version_id = version.id
    blueprint.origin_user_id = item.owner_user_id
    blueprint.install_status = install_status
    blueprint.is_dirty = False
    blueprint.updated_at = _now()


async def _overwrite_agent_blueprint_installation(
    db: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    item: MarketplaceItem,
    latest: MarketplaceVersion,
    user: CurrentUser,
) -> MarketplaceInstallation:
    if latest.payload_kind != "agent_spec":
        raise marketplace_invalid_package("latest version is not an Agent spec")
    if installation.installed_agent_blueprint_id is None:
        raise marketplace_item_not_found()

    blueprint = await db.get(AgentBlueprint, installation.installed_agent_blueprint_id)
    if blueprint is None or blueprint.user_id != user.id:
        raise marketplace_item_not_found()

    payload = _agent_blueprint_payload_with_requirements(latest)
    agent_spec = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    name = agent_spec.get("name") or payload.get("name") or item.name
    install_status, credential_bindings = await _agent_blueprint_status_from_bindings(
        db,
        version=latest,
        user=user,
        stored_bindings=blueprint.credential_bindings,
    )
    _apply_agent_payload_to_blueprint(
        blueprint=blueprint,
        item=item,
        version=latest,
        name=str(name),
        payload=payload,
        credential_bindings=credential_bindings,
        install_status=install_status,
    )
    installation.version_id = latest.id
    installation.install_status = install_status
    installation.is_dirty = False
    installation.updated_at = _now()
    return installation


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
    blueprint = (
        await db.get(AgentBlueprint, installation.installed_agent_blueprint_id)
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
        )

    if installation.resource_type == "mcp":
        return await _overwrite_mcp_installation(
            db,
            installation=installation,
            item=item,
            latest=latest,
            user=user,
        )

    if installation.resource_type == "agent":
        return await _overwrite_agent_blueprint_installation(
            db,
            installation=installation,
            item=item,
            latest=latest,
            user=user,
        )

    # ``overwrite`` — replace files in place so agent_skills rows and
    # user-specific skill references keep the same skill_id.
    if installation.resource_type != "skill":
        raise marketplace_item_not_found()
    if skill is None:
        raise marketplace_item_not_found()
    await _replace_skill_snapshot(latest, skill)
    _apply_version_metadata_to_skill(skill=skill, item=item, version=latest)
    missing = await credential_requirements.missing_required_keys(db, skill=skill, user=user)
    installation.version_id = latest.id
    installation.install_status = "needs_setup" if missing else "active"
    installation.is_dirty = False
    installation.updated_at = _now()
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
    # Keep the blueprint row (mirror skill soft-delete — accepted orphan
    # trade-off) but sync its status so the list/detail endpoints don't
    # surface it as a stale ``active`` ghost after re-install.
    if installation.installed_agent_blueprint_id is not None:
        blueprint = await db.get(
            AgentBlueprint,
            installation.installed_agent_blueprint_id,
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
    if installation.installed_mcp_server_id is not None:
        server = await db.get(McpServer, installation.installed_mcp_server_id)
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
        blueprint = await db.get(
            AgentBlueprint,
            installation.installed_agent_blueprint_id,
        )
        if blueprint is not None:
            await db.delete(blueprint)
    if installation.installed_agent_id is not None:
        agent = await db.get(Agent, installation.installed_agent_id)
        if agent is not None:
            await db.delete(agent)
    if not keep_installation:
        await db.delete(installation)


__all__: list[str] = [
    "delete_installation",
    "install_item",
    "update_installation",
]


# Silence Any-unused lint when generics aren't referenced.
_ANY_HINT: Any = None
