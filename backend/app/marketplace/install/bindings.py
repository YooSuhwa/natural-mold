"""Credential-binding validation / requirement helpers for the
marketplace install flow — BE-S3 split of ``install_service``. Function
bodies are moved verbatim; only import plumbing changed.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.error_codes import marketplace_credential_required
from app.marketplace import credential_requirements
from app.marketplace.install.common import _now
from app.models.credential import Credential
from app.models.marketplace import MarketplaceVersion, SkillCredentialBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import Skill

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


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
