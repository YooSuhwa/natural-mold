"""The journal must not open independent history by knowing another scoped run ID."""

from uuid import UUID, uuid4

import pytest
from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.authz.org_context import OrgContext
from app.config import settings
from app.exceptions import AppError
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.user import User
from app.services.ag_ui_read_access import require_read_conversation, require_read_run
from tests.authz.test_independent_job_authority import delegation as delegation

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


def bearer(user_id: UUID) -> Request:
    token = create_access_token(user_id)
    return Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


async def test_job_history_reattach_rechecks_source_lineage_and_live_parent_grant(
    db: AsyncSession, delegation: Delegation
) -> None:
    context, parent, initial, job = delegation
    initial.status, initial.is_active = "interrupted", False
    valid = ConversationRun(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=job.child_agent_id,
        conversation_id=initial.conversation_id,
        run_source="CHAT",
        source="resume",
        parent_run_id=initial.id,
        status="completed",
        is_active=False,
    )
    unrelated = ConversationRun(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        agent_id=job.child_agent_id,
        conversation_id=initial.conversation_id,
        run_source="CHAT",
        source="start",
        status="completed",
        is_active=False,
    )
    db.add_all([valid, unrelated])
    await db.commit()

    request = bearer(context.user_id)
    child = await require_read_conversation(
        db, request=request, context=context, conversation_id=job.child_conversation_id
    )
    staged = User(email="journal-pending@example.test", name="pending")
    db.add(staged)
    for run_id in (initial.id, valid.id):
        row = await require_read_run(db, context=context, conversation=child, run_id=run_id)
        assert row.id == run_id
    for run_id in (unrelated.id, parent.id, uuid4()):
        with pytest.raises(AppError) as denied:
            await require_read_run(db, context=context, conversation=child, run_id=run_id)
        assert denied.value.status == 404
        assert denied.value.code == "RESUME_NOT_FOUND"
    assert staged in db.new
    db.expunge(staged)

    await db.execute(
        delete(AgentSubAgentLink).where(
            AgentSubAgentLink.parent_agent_id == job.parent_agent_id,
            AgentSubAgentLink.sub_agent_id == job.child_agent_id,
        )
    )
    await db.commit()
    with pytest.raises(AppError) as denied:
        await require_read_conversation(
            db, request=request, context=context, conversation_id=job.child_conversation_id
        )
    assert denied.value.code == "CONVERSATION_NOT_FOUND"
    with pytest.raises(AppError) as denied:
        await require_read_run(db, context=context, conversation=child, run_id=valid.id)
    assert denied.value.code == "RESUME_NOT_FOUND"
    visible_parent = await require_read_conversation(
        db, request=request, context=context, conversation_id=parent.conversation_id
    )
    assert visible_parent.id == parent.conversation_id


async def test_poll_revalidates_expiry_even_with_cached_request_actor(
    db: AsyncSession, delegation: Delegation, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, parent, _, _ = delegation
    request = bearer(context.user_id)
    actor = await require_read_conversation(
        db, request=request, context=context, conversation_id=parent.conversation_id
    )
    assert actor.id == parent.conversation_id
    monkeypatch.setattr(settings, "access_token_expire_minutes", -5)
    expired = bearer(context.user_id)
    expired.state.current_user = request.state.current_user
    with pytest.raises(AppError) as denied:
        await require_read_conversation(
            db, request=expired, context=context, conversation_id=parent.conversation_id
        )
    assert (denied.value.status, denied.value.code) == (401, "not_authenticated")
