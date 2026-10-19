"""Installation authority checks cannot flush unrelated staged private content."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_scope import load_private_installation
from app.models.marketplace import MarketplaceInstallation
from app.models.organization import OrganizationMember
from app.models.skill import Skill

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_installation_summary_scope import InstalledFixture

pytest_plugins = ["tests.authz.test_private_installation_summary_scope"]


@pytest.mark.parametrize("revoked", [False, True])
async def test_installation_preflight_preserves_staged_writes(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    installed_fixture: InstalledFixture,
    revoked: bool,
) -> None:
    own = private_scopes.first
    skill = installed_fixture.skill
    installation_id = await db.scalar(
        select(MarketplaceInstallation.id).where(
            MarketplaceInstallation.installed_skill_id == skill.id
        )
    )
    assert installation_id is not None
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    skill.name = "Unflushed private fixture"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        result = await load_private_installation(db, installation_id, own)
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored_name = await db.scalar(select(Skill.name).where(Skill.id == skill.id))
    staged = skill in db.dirty
    await db.rollback()
    assert (result is not None) != revoked
    assert flush.call_count == 0
    assert staged
    assert stored_name != "Unflushed private fixture"
