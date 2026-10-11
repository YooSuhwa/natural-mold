"""Skill publication refreshes captured authority before snapshots and rows."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from anyio.to_thread import run_sync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AppError
from app.marketplace import publish_service
from app.marketplace.schemas import PublishSkillIn
from app.models.marketplace import MarketplaceItem
from tests.authz.test_private_publication_tool_writers import _files, suspend

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = ["tests.authz.test_private_skill_scope"]


async def test_skill_publish_denies_revoked_before_snapshot_and_item(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    org_fixture: OrgFixture,
    skill_case: SkillCase,
) -> None:
    own = private_scopes.first
    await publish_service.publish_skill(
        db,
        skill_id=skill_case.first.id,
        user=org_fixture.user,
        context=own,
        body=PublishSkillIn(name="Owned publish control", visibility="private"),
    )
    await db.commit()
    await suspend(db, private_scopes)
    before = await run_sync(_files, Path(settings.data_root))
    with pytest.raises(AppError) as captured:
        await publish_service.publish_skill(
            db,
            skill_id=skill_case.first.id,
            user=org_fixture.user,
            context=own,
            body=PublishSkillIn(name="Revoked skill publish", visibility="private"),
        )
    assert captured.value.status == 403
    await db.commit()
    assert (
        await db.scalar(
            select(MarketplaceItem.id).where(MarketplaceItem.name == "Revoked skill publish")
        )
        is None
    )
    assert await run_sync(_files, Path(settings.data_root)) == before
