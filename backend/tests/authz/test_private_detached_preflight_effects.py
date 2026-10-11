"""Independent shared queue/evaluation preflight witnesses on frozen source."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.organization import OrganizationMember
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.services import conversation_run_queue_mutations as queue
from app.services import skill_evaluation_worker_state as evaluation
from app.services.chat import conversations
from app.services.skill_evaluation_worker_types import SkillEvaluationExecutionError

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


async def test_queue_lock_does_not_flush_before_revoked_actor_denial(
    db: AsyncSession, private_scopes: PrivateScopes, api_case: ApiCase
) -> None:
    own = private_scopes.first
    conversation = await conversations.create_conversation(db, api_case.first.agent_id, context=own)
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="removed")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    agent = await db.get(Agent, api_case.first.agent_id)
    assert agent is not None
    original = agent.name
    agent.name = "F2 revoked queued staged content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        with pytest.raises(HTTPException) as error:
            await queue.lock_owned_conversation(db, conversation_id=conversation.id, context=own)
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Agent.name).where(Agent.id == agent.id))
    await db.rollback()
    assert error.value.status_code == 404
    assert flush.call_count == 0
    assert stored == original


async def test_evaluation_preflight_does_not_flush_before_revoked_actor_denial(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    own = private_scopes.first
    skill = Skill(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="F2 original",
        slug="f2-evaluation-original",
        kind="text",
    )
    db.add(skill)
    await db.flush()
    eval_set = SkillEvaluationSet(user_id=own.user_id, skill_id=skill.id, name="F2", evals=[])
    db.add(eval_set)
    await db.flush()
    run = SkillEvaluationRun(user_id=own.user_id, skill_id=skill.id, evaluation_set_id=eval_set.id)
    db.add(run)
    await db.commit()
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="removed")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    skill.name = "F2 revoked evaluation staged content"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        with pytest.raises(SkillEvaluationExecutionError):
            await evaluation.build_context(db, run)
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Skill.name).where(Skill.id == skill.id))
    await db.rollback()
    assert flush.call_count == 0
    assert stored == "F2 original"
