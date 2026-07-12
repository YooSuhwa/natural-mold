"""MCP-type install / overwrite logic — BE-S3 split of
``install_service``. Function bodies are moved verbatim; only import
plumbing changed.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.error_codes import (
    marketplace_credential_required,
    marketplace_invalid_package,
    marketplace_item_not_found,
)
from app.marketplace.install.bindings import (
    _apply_mcp_payload_to_server,
    _credential_requirements_by_key,
    _materialize_mcp_tool_snapshot,
    _mcp_required_keys,
    _required_credential_keys,
    _validate_mcp_bindings,
)
from app.marketplace.install.common import _existing_installation, _now
from app.marketplace.schemas import InstallMarketplaceItemIn
from app.models.credential import Credential
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceVersion,
)
from app.models.mcp_server import McpServer

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


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
