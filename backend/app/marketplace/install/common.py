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

from app.error_codes import marketplace_version_not_found
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


def _payload_skill_kind(version: MarketplaceVersion) -> str:
    """Decide the installed skill's ``kind`` from the version payload.

    ``version.payload`` carries arbitrary metadata; the publish flow
    records ``{"kind": "text"|"package", ...}``. Default to ``package``
    because that's the safe-superset (a single-file package is valid).
    """

    payload = version.payload or {}
    kind = payload.get("kind") or "package"
    return "package" if kind not in ("text", "package") else kind
