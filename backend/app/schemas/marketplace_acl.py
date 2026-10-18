"""Named recipients of a restricted marketplace item (S16 marketplace share dialog)."""

from uuid import UUID

from pydantic import BaseModel


class MarketplaceAclEntry(BaseModel):
    user_id: UUID
    display_name: str | None = None
    permission: str


class MarketplaceAclList(BaseModel):
    items: list[MarketplaceAclEntry]
