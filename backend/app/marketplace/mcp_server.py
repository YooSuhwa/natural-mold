"""Marketplace support for MCP server resources."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.error_codes import (
    marketplace_acl_required,
    marketplace_invalid_package,
    marketplace_invalid_visibility,
    marketplace_item_not_found,
    marketplace_manage_forbidden,
)
from app.marketplace.access import can_manage_item
from app.marketplace.payloads import (
    canonical_json_bytes,
    canonical_json_hash,
)
from app.marketplace.publish_common import (
    clean_mapping as _clean_mapping,
)
from app.marketplace.publish_common import (
    contains_credential_placeholder as _contains_credential_placeholder,
)
from app.marketplace.publish_common import (
    create_acl as _create_acl,
)
from app.marketplace.publish_common import (
    next_version_number as _next_version_number,
)
from app.marketplace.publish_common import (
    now as _now,
)
from app.marketplace.publish_common import (
    raise_if_mcp_config_has_literal_secrets as _raise_if_mcp_config_has_literal_secrets,
)
from app.marketplace.publish_common import (
    raise_if_payload_has_secrets as _raise_if_payload_has_secrets,
)
from app.marketplace.publish_common import (
    slugify,
    upsert_publication_link,
)
from app.marketplace.schemas import PublishMcpServerIn
from app.models.credential import Credential
from app.models.marketplace import (
    MarketplaceItem,
    MarketplaceItemACL,
    MarketplaceVersion,
)
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


def _slugify(value: str) -> str:
    return slugify(value, fallback="mcp")


def _clean_tool_snapshot(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for tool in tools or []:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        cleaned.append(
            {
                "name": name,
                "description": tool.get("description"),
                "input_schema": tool.get("input_schema") or {},
            }
        )
    return cleaned


async def _tool_snapshot_for_server(
    db: AsyncSession, *, server_id: uuid.UUID
) -> list[dict[str, Any]]:
    tools = (
        await db.execute(
            select(McpTool)
            .where(McpTool.server_id == server_id)
            .where(McpTool.enabled.is_(True))
            .order_by(McpTool.name.asc())
        )
    ).scalars().all()
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema or {},
        }
        for tool in tools
    ]


def build_mcp_server_payload(
    server: McpServer,
    *,
    credential_definition_key: str | None,
    tool_snapshot: list[dict[str, Any]] | None = None,
    registry_key: str | None = None,
) -> dict[str, Any]:
    """Build a portable, secret-free marketplace payload from ``McpServer``.

    Runtime columns and database identifiers are intentionally excluded.
    The resulting payload is suitable for ``MarketplaceVersion.payload``
    with ``payload_kind='mcp_template'``.
    """

    is_stdio = server.transport == "stdio"
    env_vars = _clean_mapping(server.env_vars)
    headers = _clean_mapping(server.headers)
    args = list(server.args or [])
    _raise_if_mcp_config_has_literal_secrets(
        server_name=server.name,
        env_vars=env_vars,
        headers=headers,
        args=args,
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "resource": "mcp_server",
        "name": server.name,
        "description": server.description,
        "transport": server.transport,
        "url": server.url,
        "command": server.command,
        "args": args,
        "env_vars": env_vars,
        "headers": headers,
        "credential_definition_key": credential_definition_key,
        "registry_key": registry_key,
        "tool_snapshot": _clean_tool_snapshot(tool_snapshot),
        "install_defaults": {
            "discover_on_install": True,
            "enabled_tool_names": [
                tool["name"] for tool in _clean_tool_snapshot(tool_snapshot)
            ],
        },
        "security": {
            "requires_network": server.transport in {"sse", "streamable_http"},
            "stdio_risk": is_stdio,
            "support_level": "manual_only" if is_stdio else "one_click",
        },
    }
    _raise_if_payload_has_secrets(payload)
    return payload


def _credential_requirements(
    credential_definition_key: str | None,
) -> list[dict[str, Any]] | None:
    if credential_definition_key is None:
        return None
    return [
        {
            "key": "mcp_auth",
            "definition_key": credential_definition_key,
            "required": True,
            "label": "MCP credential",
            "description": "Credential used to authenticate the MCP server",
            "fields": [],
            "injection": "config",
            "scope": "user",
        }
    ]


async def publish_mcp_server(
    db: AsyncSession,
    *,
    server_id: uuid.UUID,
    user: CurrentUser,
    body: PublishMcpServerIn,
) -> MarketplaceItem:
    server = await db.get(McpServer, server_id)
    if server is None or server.user_id != user.id:
        raise marketplace_item_not_found()

    if body.visibility not in ("private", "restricted", "public", "unlisted"):
        raise marketplace_invalid_visibility(
            f"unsupported publish visibility: {body.visibility}"
        )
    if body.visibility == "restricted" and not body.acl_user_ids:
        raise marketplace_acl_required()
    if server.transport == "stdio" and body.visibility in {"public", "unlisted"}:
        raise marketplace_invalid_package(
            "stdio MCP servers can only be shared privately or with explicit ACL"
        )

    item: MarketplaceItem | None = None
    if body.item_id is not None:
        item = await db.get(MarketplaceItem, body.item_id)
        if item is None:
            raise marketplace_item_not_found()
        if not can_manage_item(item, user):
            raise marketplace_manage_forbidden()
        if item.resource_type != "mcp":
            raise marketplace_invalid_package("marketplace item is not an MCP item")
    else:
        slug = _slugify(body.name)
        item = (
            await db.execute(
                select(MarketplaceItem)
                .where(MarketplaceItem.owner_user_id == user.id)
                .where(MarketplaceItem.resource_type == "mcp")
                .where(MarketplaceItem.slug == slug)
                .limit(1)
            )
        ).scalar_one_or_none()
        if item is None:
            item = MarketplaceItem(
                id=uuid.uuid4(),
                resource_type="mcp",
                owner_user_id=user.id,
                is_system=False,
                is_listed=False,
                name=body.name,
                slug=slug,
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
        elif not can_manage_item(item, user):
            raise marketplace_manage_forbidden()

    credential_definition_key: str | None = None
    if server.credential_id is not None:
        credential = await db.get(Credential, server.credential_id)
        if credential is not None and credential.user_id == user.id:
            credential_definition_key = credential.definition_key
    if credential_definition_key is None and (
        _contains_credential_placeholder(server.headers)
        or _contains_credential_placeholder(server.env_vars)
    ):
        raise marketplace_invalid_package(
            "MCP server uses credential interpolation but has no bound credential"
        )

    tools = (
        await _tool_snapshot_for_server(db, server_id=server.id)
        if body.include_tool_snapshot
        else []
    )
    payload = build_mcp_server_payload(
        server,
        credential_definition_key=credential_definition_key,
        tool_snapshot=tools,
    )
    content_bytes = canonical_json_bytes(payload)
    content_hash = canonical_json_hash(payload)

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
        version = existing_version
    else:
        version_number = await _next_version_number(db, item.id)
        version = MarketplaceVersion(
            id=uuid.uuid4(),
            item_id=item.id,
            version_label=f"mcp-{version_number}",
            version_number=version_number,
            resource_type="mcp",
            payload_kind="mcp_template",
            payload=payload,
            storage_path=None,
            content_hash=content_hash,
            size_bytes=len(content_bytes),
            credential_requirements=_credential_requirements(credential_definition_key),
            execution_profile=payload.get("security"),
            release_notes=body.release_notes,
            created_by=user.id,
        )
        db.add(version)
        await db.flush()

    item.latest_version_id = version.id
    item.visibility = body.visibility
    item.status = "published"
    item.published_at = _now()
    item.updated_at = _now()
    if body.item_id is None:
        item.name = body.name
        item.slug = _slugify(body.name)
        item.description = body.description
        item.icon_id = body.icon_id
        item.tags = list(body.tags) or None
        item.categories = list(body.categories) or None
    else:
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
        existing_acl = (
            await db.execute(
                select(MarketplaceItemACL).where(
                    MarketplaceItemACL.item_id == item.id
                )
            )
        ).scalars().all()
        for row in existing_acl:
            await db.delete(row)

    await upsert_publication_link(
        db,
        item=item,
        user_id=user.id,
        resource_type="mcp",
        source_mcp_server_id=server.id,
    )
    await db.flush()
    return item


__all__ = ["build_mcp_server_payload", "publish_mcp_server"]
