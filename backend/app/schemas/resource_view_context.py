"""Explicit actor and organization passed to private resource response enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.authz.org_context import OrgContext
    from app.dependencies import CurrentUser


@dataclass(frozen=True, slots=True)
class ResourceViewContext:
    user: CurrentUser
    context: OrgContext
