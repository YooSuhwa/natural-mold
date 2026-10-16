"""The installation-update source decision (D-05 item gate; E-09 review).

Legacy (``authorize is None``) keeps the existing loader + ``can_install_item``. Shadow
samples the legacy answer; enforce asks OpenFGA ``can_install`` on the item's own
candidate, so a withdrawn or narrowed company publication stops other organizations'
installations from pulling new versions.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.error_codes import marketplace_item_not_found
from app.marketplace.access import ItemAuthorizer, can_install_item
from app.marketplace.install.common import load_install_source_item
from app.models.marketplace import MarketplaceItem


async def installable_source(
    db: AsyncSession,
    item_id: uuid.UUID,
    context: OrgContext,
    user: CurrentUser,
    authorize: ItemAuthorizer | None,
) -> MarketplaceItem:
    if authorize is not None and authorize.enforced:
        return await authorize(item_id, None)
    item = await load_install_source_item(db, item_id, context)
    if authorize is not None:
        return await authorize(item_id, item)
    if not can_install_item(item, user):
        raise marketplace_item_not_found()
    return item
