"""Shared helpers for the Agent / MCP marketplace publish services.

``agent_spec`` and ``mcp_server`` publish JSON snapshot payloads and
share the same item/version/ACL/publication-link bookkeeping. This
module holds the common pieces so the two services stay in sync.

Note: ``install_service`` keeps its own private ``_slugify`` / ``_now``
copies on purpose (documented module boundary) — do not fold those in.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.error_codes import marketplace_secret_detected
from app.marketplace.payloads import scan_payload
from app.marketplace.secret_scan import is_suspicious_secret_value
from app.models.marketplace import (
    MarketplaceItem,
    MarketplaceItemACL,
    MarketplacePublicationLink,
    MarketplaceVersion,
)


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def slugify(value: str, *, fallback: str) -> str:
    base = value.strip().lower().replace("_", "-").replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9-]+", "-", base).strip("-")
    return cleaned or fallback


# NOTE: this regex is *searched* inside values to detect that a template
# uses credential interpolation at all (publish gate: a placeholder
# requires a bound credential). It intentionally differs from
# ``payloads._PLACEHOLDER_RE`` which is a *full-match* allowlist — the
# whole string must be a placeholder for the secret scan to skip it.
CREDENTIAL_PLACEHOLDER_RE = re.compile(
    r"=?\{\{\s*\$credentials\.[A-Za-z_][A-Za-z0-9_]*\s*\}\}"
)


def contains_credential_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return bool(CREDENTIAL_PLACEHOLDER_RE.search(value))
    if isinstance(value, dict):
        return any(contains_credential_placeholder(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(contains_credential_placeholder(item) for item in value)
    return False


def clean_mapping(value: dict | None) -> dict[str, Any]:
    if not value:
        return {}
    return {str(k): v for k, v in value.items() if v is not None}


def raise_if_payload_has_secrets(payload: dict[str, Any]) -> None:
    findings = scan_payload(payload)
    if not findings:
        return
    summary = ", ".join(f"{finding.path} ({finding.kind})" for finding in findings[:5])
    raise marketplace_secret_detected(f"payload contains potential secrets: {summary}")


def raise_if_mcp_config_has_literal_secrets(
    *,
    server_name: str,
    env_vars: dict | None,
    headers: dict | None,
    args: list | None = None,
) -> None:
    """Allowlist gate for MCP ``env_vars`` / ``headers`` / ``args`` on publish.

    The blocklist ``scan_payload`` misses opaque/custom secrets, so each
    literal value is run through ``is_suspicious_secret_value``. A flagged
    value blocks publication with an actionable hint to switch to
    ``{{$credentials.x}}`` interpolation. ``args`` list entries are checked
    too: a ``["--token", "<secret>"]`` style command line is just as leaky
    as an env var, and they have no header name so each string is judged on
    its value alone.
    """

    offenders: list[str] = []
    for key, value in (env_vars or {}).items():
        if is_suspicious_secret_value(value):
            offenders.append(f"env_vars.{key}")
    for key, value in (headers or {}).items():
        if is_suspicious_secret_value(value, header_name=str(key)):
            offenders.append(f"headers.{key}")
    for index, value in enumerate(args or []):
        if is_suspicious_secret_value(value):
            offenders.append(f"args[{index}]")
    if not offenders:
        return
    summary = ", ".join(offenders[:5])
    raise marketplace_secret_detected(
        f"MCP server '{server_name}' has literal secrets in {summary}; "
        "do not embed secrets — use {{$credentials.x}} interpolation instead "
        "and bind a credential before publishing"
    )


def raise_if_agent_base_url_has_literal_secret(
    *,
    label: str,
    base_url: object,
) -> None:
    """Allowlist gate for an agent model's ``base_url`` on publish.

    A ``base_url`` is normally a bare endpoint (``https://api.host/v1``),
    but an operator could paste a signed/credential-bearing URL. Reuse the
    same allowlist heuristic so the agent blueprint path can't smuggle a
    secret the standalone MCP path already rejects.
    """

    if not is_suspicious_secret_value(base_url):
        return
    raise marketplace_secret_detected(
        f"{label} base_url embeds a literal secret; "
        "do not embed secrets — use {{$credentials.x}} interpolation instead "
        "and bind a credential before publishing"
    )


async def next_version_number(db: AsyncSession, item_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(MarketplaceVersion.version_number), 0)).where(
            MarketplaceVersion.item_id == item_id
        )
    )
    return int(result.scalar_one() or 0) + 1


async def create_acl(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    user_ids: list[uuid.UUID],
    permission: str = "install",
) -> None:
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
                item_id=item.id,
                user_id=user_id,
                permission=permission,
            )
        )


async def upsert_publication_link(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    user_id: uuid.UUID,
    resource_type: str,
    source_agent_id: uuid.UUID | None = None,
    source_mcp_server_id: uuid.UUID | None = None,
    source_skill_id: uuid.UUID | None = None,
) -> None:
    row = (
        await db.execute(
            select(MarketplacePublicationLink).where(
                MarketplacePublicationLink.item_id == item.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            MarketplacePublicationLink(
                user_id=user_id,
                item_id=item.id,
                resource_type=resource_type,
                source_agent_id=source_agent_id,
                source_mcp_server_id=source_mcp_server_id,
                source_skill_id=source_skill_id,
            )
        )
        return
    row.user_id = user_id
    row.resource_type = resource_type
    row.source_agent_id = source_agent_id
    row.source_mcp_server_id = source_mcp_server_id
    row.source_skill_id = source_skill_id
    row.updated_at = now()


__all__ = [
    "CREDENTIAL_PLACEHOLDER_RE",
    "clean_mapping",
    "contains_credential_placeholder",
    "create_acl",
    "next_version_number",
    "now",
    "raise_if_agent_base_url_has_literal_secret",
    "raise_if_mcp_config_has_literal_secrets",
    "raise_if_payload_has_secrets",
    "slugify",
    "upsert_publication_link",
]
