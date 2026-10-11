"""Run services inherit private ownership and scope from persisted conversations."""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.services import conversation_run_service as runs
from app.services.conversation_run_message_links import RunMessageLinkQuery, list_run_message_links
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.asyncio
async def test_same_owner_foreign_org_cannot_read_or_create_runs(db: AsyncSession) -> None:
    # Given one private conversation and a run in organization A.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="queued",
        is_active=True,
    )
    db.add(run)
    await db.flush()
    foreign = replace(unit_context(TEST_USER_ID), org_id=uuid4())

    # When the same owner queries from another explicit organization.
    with pytest.raises(AppError) as active_denied:
        await runs.get_active_run(db, conversation_id=conversation.id, context=foreign)
    with pytest.raises(AppError) as latest_denied:
        await runs.latest_run_for_conversation(db, conversation_id=conversation.id, context=foreign)
    with pytest.raises(AppError) as batch_denied:
        await runs.active_runs_for_conversations(db, [conversation.id], foreign)

    # This invented organization has no membership; authority denies before reading rows.
    assert (
        active_denied.value.status == latest_denied.value.status == batch_denied.value.status == 403
    )
    with pytest.raises(HTTPException) as rejected:
        await runs.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            context=foreign,
            source="chat",
            input_preview="Foreign scope",
        )
    assert rejected.value.status_code == 404


@pytest.mark.asyncio
async def test_message_run_links_are_absent_in_same_owner_foreign_org(db: AsyncSession) -> None:
    # Given a historical message linked to a private conversation run.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="completed",
        is_active=False,
    )
    db.add(run)
    await db.flush()
    message_id = str(uuid4())
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run.id),
            linked_message_ids=[message_id],
            events=[],
        )
    )
    await db.flush()
    # When the same owner requests the link from another organization.
    result = await list_run_message_links(
        db,
        RunMessageLinkQuery(
            conversation_id=conversation.id,
            context=replace(unit_context(TEST_USER_ID), org_id=uuid4()),
            message_ids=[message_id],
        ),
    )
    # Then the private run identifier is absent.
    assert result == []


@pytest.mark.asyncio
async def test_foreign_conversation_resume_child_does_not_hide_interrupted_parent(
    db: AsyncSession,
) -> None:
    # Given an interrupted parent and an invalid resume link from another conversation.
    conversation_id = await seed_conversation_with_agent()
    foreign_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    foreign = await db.get(Conversation, foreign_id)
    assert conversation is not None and foreign is not None
    parent = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="interrupted",
        is_active=False,
    )
    db.add(parent)
    await db.flush()
    db.add(
        ConversationRun(
            conversation_id=foreign.id,
            agent_id=foreign.agent_id,
            user_id=TEST_USER_ID,
            parent_run_id=parent.id,
            source="resume",
            status="completed",
            is_active=False,
        )
    )
    await db.flush()
    # When unresolved approval metadata is queried in the parent's organization.
    result = await runs.get_latest_interrupted_run(
        db, conversation_id=conversation.id, context=unit_context(TEST_USER_ID)
    )
    # Then the unrelated resume link cannot suppress the genuine interrupted parent.
    assert result is not None and result.id == parent.id
