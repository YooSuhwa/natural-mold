"""Shared install-flow helpers (non-snapshot) — BE-S3 split of
``install_service``. Function bodies are moved verbatim; only import
plumbing changed.

Note: ``_payload_skill_kind`` lives here (the BE-S3 plan slotted it in
``skill.py``) because ``snapshot._rel_install_storage`` needs it while
``skill._apply_version_metadata_to_skill`` needs ``snapshot`` — keeping
it in ``skill`` would create a snapshot ↔ skill import cycle.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.authz.catalog_scope import (
    catalog_view_candidate_predicate,
    installation_child_scope_predicate,
)
from app.authz.catalog_version_scope import load_item_version
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate
from app.error_codes import marketplace_item_not_found, marketplace_version_not_found
from app.exceptions import AppError
from app.marketplace.access import is_owner
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceVersion,
)

if TYPE_CHECKING:
    from app.dependencies import CurrentUser


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

    target_id = version_id if version_id is not None else item.latest_version_id
    if target_id is None:
        raise marketplace_version_not_found()
    version = await load_item_version(db, item.id, target_id)
    if version is None:
        raise marketplace_version_not_found()
    return version


async def load_install_source_item(
    db: AsyncSession, item_id: uuid.UUID, context: OrgContext
) -> MarketplaceItem:
    """Load the current catalog source without flushing pending caller writes."""
    statement = (
        select(MarketplaceItem)
        .where(MarketplaceItem.id == item_id, catalog_view_candidate_predicate(context))
        .execution_options(autoflush=False, populate_existing=True)
        .options(selectinload(MarketplaceItem.acl_entries))
    )
    with db.no_autoflush:
        item = await db.scalar(statement)
    if item is None:
        raise marketplace_item_not_found()
    return item


async def _existing_installation(
    db: AsyncSession,
    *,
    item: MarketplaceItem,
    user: CurrentUser,
    context: OrgContext,
) -> MarketplaceInstallation | None:
    stmt = (
        select(MarketplaceInstallation, installation_child_scope_predicate(context))
        .where(
            MarketplaceInstallation.item_id == item.id,
            private_scope_predicate(MarketplaceInstallation, context),
            MarketplaceInstallation.install_status != "uninstalled",
        )
        .order_by(MarketplaceInstallation.installed_at.desc())
        .limit(1)
    )
    result = (await db.execute(stmt)).first()
    if result is None:
        return None
    installation, children_authorized = result
    if not children_authorized:
        raise marketplace_item_not_found()
    try:
        await resolve_private_actor(
            db,
            PrivateResourceActor(installation.user_id, installation.org_id, installation.tenant_id),
        )
    except AppError:
        raise marketplace_item_not_found() from None
    return installation


def _payload_skill_kind(version: MarketplaceVersion) -> str:
    """Decide the installed skill's ``kind`` from the version payload.

    ``version.payload`` carries arbitrary metadata; the publish flow
    records ``{"kind": "text"|"package", ...}``. Default to ``package``
    because that's the safe-superset (a single-file package is valid).
    """

    payload = version.payload or {}
    kind = payload.get("kind") or "package"
    return "package" if kind not in ("text", "package") else kind
