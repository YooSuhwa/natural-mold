"""Independent exact-R9 pre-effect checks for persisted Skill Builder source edges."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillBuilderMode
from app.services import skill_builder_service
from app.services.skill_builder_errors import SkillBuilderValidationError

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = ["tests.authz.test_private_skill_scope"]


async def test_foreign_improvement_source_denied_before_validation_write(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    skill_case: SkillCase,
) -> None:
    # Given an actual owned improvement session and an owned malformed-draft control.
    own = private_scopes.first
    session = await skill_builder_service.create_session(
        db,
        context=own,
        user_request="Owned improvement fixture",
        mode=SkillBuilderMode.IMPROVE,
        source_skill_id=skill_case.first.id,
    )
    session.draft_package = {
        "name": "Fixture",
        "slug": "fixture",
        "description": "Fixture",
        "files": [],
    }
    await db.commit()
    session_id = session.id
    foreign_skill_id = skill_case.second.id
    with pytest.raises(SkillBuilderValidationError):
        await skill_builder_service.confirm_session(db, session, context=own)
    await db.rollback()
    await db.execute(
        update(SkillBuilderSession)
        .where(SkillBuilderSession.id == session_id)
        .values(source_skill_id=foreign_skill_id, validation_result=None, status="collecting")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    persisted_parent = await db.scalar(
        select(SkillBuilderSession.source_skill_id).where(SkillBuilderSession.id == session_id)
    )
    assert persisted_parent == foreign_skill_id
    # When confirmation receives the valid owner/context after persisted parent corruption.
    denied = False
    validation_reached = False
    try:
        session = await skill_builder_service.get_session(db, session_id, own)
        if session is None:
            denied = True
        else:
            await skill_builder_service.confirm_session(db, session, context=own)
    except AppError:
        denied = True
    except SkillBuilderValidationError:
        validation_reached = True
    await db.commit()
    # Then authorization must reject before validation/status can be persisted.
    stored = (
        await db.execute(
            select(SkillBuilderSession.validation_result, SkillBuilderSession.status).where(
                SkillBuilderSession.id == session_id
            )
        )
    ).one()
    assert denied and not validation_reached and stored.validation_result is None
