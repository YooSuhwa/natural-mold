"""Builder mutation targets reload exact scope and current membership."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.services import skill_builder_service as service
from app.services.skill_builder_mutation_scope import (
    SkillBuilderEvaluation,
    SkillBuilderMessage,
    SkillBuilderMutationTarget,
)

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]
Operation = Literal["consent", "message", "draft", "validation", "trigger"]


@pytest.mark.parametrize("operation", ["consent", "message", "draft", "validation", "trigger"])
async def test_revoked_builder_mutation_denies_before_state_changes(
    db: AsyncSession, private_scopes: PrivateScopes, operation: Operation
) -> None:
    own = private_scopes.first
    session = await service.create_session(db, context=own, user_request="Boundary fixture")
    await db.commit()
    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id
        )
    )
    await db.commit()
    target = SkillBuilderMutationTarget(session.id, own)
    match operation:
        case "consent":
            pending = service.record_tool_consents(db, target, ["test_skill_draft"])
        case "message":
            pending = service.append_message(db, target, SkillBuilderMessage("user", "Denied"))
        case "draft":
            pending = service.save_draft_package(db, target, {"marker": "Denied"})
        case "validation":
            pending = service.save_validation_result(db, target, {"marker": "Denied"})
        case "trigger":
            pending = service.save_trigger_eval_result(
                db, target, SkillBuilderEvaluation({"marker": "Denied"}, {"marker": "Denied"})
            )
        case unreachable:
            assert_never(unreachable)
    with pytest.raises(AppError):
        await pending
    await db.commit()
    await db.refresh(session)
    assert not session.tool_consents and not session.messages and not session.draft_package
    assert not session.validation_result and not session.trigger_eval_result
