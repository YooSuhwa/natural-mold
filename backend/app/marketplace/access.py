"""Marketplace access control matrix (Spec §12).

Pure functions: no DB writes, no side effects beyond optional read-only
queries the caller already provided. Routers and services consult this
module to decide visibility / install / manage permissions.

Three predicates cover the API surface:

* ``can_view_item``     — "should this item appear in catalog / detail?"
* ``can_install_item``  — "is this user allowed to install/clone this?"
* ``can_manage_item``   — "owner or super_user — allowed to publish/ACL?"

Routers must collapse "not found" and "forbidden" to a single 404 to
avoid enumeration oracle behavior (rules/security.md). Use the helpers
here to *decide* and emit the same error code in either case.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.dependencies import CurrentUser
    from app.models.marketplace import MarketplaceItem, MarketplaceItemACL


def _acl_perms_for(
    acl_entries: Iterable[MarketplaceItemACL], user_id: uuid.UUID
) -> set[str]:
    """Permission set granted to ``user_id`` by the item's ACL rows."""

    return {entry.permission for entry in acl_entries if entry.user_id == user_id}


def is_owner(item: MarketplaceItem, user: CurrentUser) -> bool:
    """Item owner (None ⇒ system item, no owner)."""

    return item.owner_user_id is not None and item.owner_user_id == user.id


def can_view_item(
    item: MarketplaceItem,
    user: CurrentUser,
    *,
    acl_entries: Iterable[MarketplaceItemACL] | None = None,
) -> bool:
    """True when the user is allowed to see this item in catalog/detail.

    Rules (Spec §12):

    * super_user             — always.
    * is_system / system     — always (system listing).
    * owner                  — always.
    * disabled               — owner + super_user only (handled above).
    * public, unlisted       — anyone authenticated.
    * restricted             — ACL membership required.
    * private                — owner only (already covered).
    """

    if user.is_super_user:
        return True
    if is_owner(item, user):
        return True
    if item.status == "disabled":
        return False
    if item.visibility == "system":
        return True
    if item.visibility in ("public", "unlisted"):
        return item.status == "published"
    if item.visibility == "restricted":
        perms = _acl_perms_for(acl_entries or item.acl_entries, user.id)
        # Any ACL membership grants *visibility*; install needs the install
        # bit, manage needs the manage bit.
        return bool(perms) and item.status == "published"
    # visibility == "private"
    return False


def can_install_item(
    item: MarketplaceItem,
    user: CurrentUser,
    *,
    acl_entries: Iterable[MarketplaceItemACL] | None = None,
) -> bool:
    """True when the user can clone/install this item.

    Owner can install their own items (useful for re-installing a clean
    copy after dirty edits). Disabled items reject install for everyone
    except super_user (so admins can rescue a broken state).
    """

    if user.is_super_user:
        return True
    if item.status == "disabled":
        return False
    if not can_view_item(item, user, acl_entries=acl_entries):
        return False
    if item.visibility == "restricted":
        perms = _acl_perms_for(acl_entries or item.acl_entries, user.id)
        return "install" in perms or "manage" in perms
    # private — only the owner reaches here, and they can install their
    # own draft for testing.
    if item.visibility == "private":
        return is_owner(item, user)
    # public, unlisted, system — anyone who can view.
    return True


def can_manage_item(item: MarketplaceItem, user: CurrentUser) -> bool:
    """Owner / super_user only. Used by publish/ACL/disable routes."""

    if user.is_super_user:
        return True
    if is_owner(item, user):
        return True
    # Explicit "manage" ACL grant is an escape hatch for delegated admin
    # but Phase 1 doesn't expose it via UI — keep the check anyway so
    # future flows pick it up automatically.
    perms = _acl_perms_for(item.acl_entries, user.id)
    return "manage" in perms


__all__ = [
    "can_install_item",
    "can_manage_item",
    "can_view_item",
    "is_owner",
]
