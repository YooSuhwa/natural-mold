"""D-10 legacy gaps: feedback conversation/message membership and the organization's
``allow_public_share_links`` policy on public share creation and reads (plan 9.3)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.organization import Organization
from app.services.thread_branch_service import MessageTree, MessageTreeNode
from tests.authz.agent_route_fixture import seed_agent
from tests.authz.d04_support import OTHER_USER_ID, seed_scope
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _conversation(db: AsyncSession, owner: uuid.UUID = TEST_USER_ID) -> uuid.UUID:
    agent = await db.get(Agent, uuid.UUID(await seed_agent(owner=owner)))
    assert agent is not None
    conversation = Conversation(
        agent_id=agent.id,
        title="c",
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=owner,
        agent_name_snapshot=agent.name,
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.commit()
    return conversation.id


def _tree(*messages) -> MessageTree:
    nodes = [
        MessageTreeNode(message=m, parent_id=None, introduced_by_checkpoint_id="cp")
        for m in messages
    ]
    return MessageTree(nodes=nodes, active_tip_message_id=None, active_checkpoint_id="cp")


def _checkpoint(tree: MessageTree):
    return (
        patch("app.agent_runtime.checkpointer.get_checkpointer"),
        patch(
            "app.services.thread_branch_service.build_message_tree", AsyncMock(return_value=tree)
        ),
    )


async def _rate(client: AsyncClient, message_id: str, conversation_id: uuid.UUID):
    body = {"rating": "up", "conversation_id": str(conversation_id)}
    return await client.post(f"/api/messages/{message_id}/feedback", json=body)


async def test_feedback_on_a_foreign_conversation_is_404(client: AsyncClient, db) -> None:
    foreign = await _conversation(db, OTHER_USER_ID)
    first, second = _checkpoint(_tree(AIMessage(id="answer-1", content="hi")))
    with first, second:
        response = await _rate(client, "answer-1", foreign)
    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "CONVERSATION_NOT_FOUND",
    )


async def test_feedback_needs_the_message_in_that_conversation(client: AsyncClient, db) -> None:
    conversation = await _conversation(db)
    tree = _tree(HumanMessage(id="ask-1", content="q"), AIMessage(id="answer-1", content="a"))
    public_id = str(parse_msg_id("answer-1", conversation, 1))
    first, second = _checkpoint(tree)
    with first, second:
        stray = await _rate(client, "other-message", conversation)
        raw = await _rate(client, "answer-1", conversation)
        public = await _rate(client, public_id, conversation)
    assert (stray.status_code, stray.json()["error"]["code"]) == (404, "MESSAGE_NOT_FOUND")
    assert raw.status_code == 200 and public.status_code == 200


async def _disable_public_links(db: AsyncSession) -> None:
    org = await db.get(Organization, TEST_ORG_ID)
    assert org is not None
    org.settings = {**(org.settings or {}), "allow_public_share_links": False}
    await db.commit()


async def test_public_share_creation_respects_the_org_policy(client: AsyncClient, db) -> None:
    conversation = await _conversation(db)
    await _disable_public_links(db)
    response = await client.post(f"/api/conversations/{conversation}/share")
    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "PUBLIC_SHARE_LINKS_DISABLED",
    )


async def test_public_share_reads_stop_when_the_org_disables_links(client: AsyncClient, db) -> None:
    conversation = await _conversation(db)
    token = (await client.post(f"/api/conversations/{conversation}/share")).json()["share_token"]
    with patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=AsyncMock(return_value=[]),
    ):
        assert (await client.get(f"/api/shares/{token}")).status_code == 200  # warms the cache
        await _disable_public_links(db)
        view = await client.get(f"/api/shares/{token}")
        messages = await client.get(f"/api/shares/{token}/messages")
        artifacts = await client.get(f"/api/shares/{token}/artifacts")
    for response in (view, messages, artifacts):
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "SHARE_NOT_FOUND",
        )
