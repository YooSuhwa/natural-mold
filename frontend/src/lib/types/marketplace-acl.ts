/** `GET /api/marketplace/items/{id}/acl` (`backend/app/schemas/marketplace_acl.py`). */
export interface MarketplaceAclEntry {
  user_id: string
  display_name: string | null
  permission: string
}

export interface MarketplaceAclList {
  items: MarketplaceAclEntry[]
}
