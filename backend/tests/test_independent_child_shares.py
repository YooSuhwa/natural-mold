from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import load_private_conversation
from app.authz.org_context import OrgContext
from app.config import settings
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import Organization
from app.services import share_cache
from app.services.share_public_view import require_public_share
from app.services.thread_branch_service import MessageTree, MessageTreeNode
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.test_share_artifacts_router import _create_artifact_for_conversation

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("revocation", ["parent_deleted", "edge_removed"])
async def test_issued_share_survives_delegation_loss_until_owner_revokes_it(
    client: AsyncClient,
    raw_client: AsyncClient,
    db: AsyncSession,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revocation: str,
) -> None:
    context, parent, child, job = delegation
    conversation_id = job.child_conversation_id
    management = f"/api/conversations/{conversation_id}/share"
    message_id = str(uuid4())
    node = MessageTreeNode(AIMessage(id=message_id, content="Published result"), None, "cp")
    tree = AsyncMock(return_value=MessageTree([node], message_id, "cp"))
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: object())
    monkeypatch.setattr("app.services.thread_branch_service.build_message_tree", tree)
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "store"))
    _, artifact_id = await _create_artifact_for_conversation(
        tmp_path,
        conv_id=conversation_id,
        agent_id=job.child_agent_id,
        assistant_msg_id=str(child.id),
        filename="shared.txt",
        content="Published bytes",
        linked_message_ids=[message_id],
    )
    issued = await client.post(management)
    assert issued.status_code == 200
    token = issued.json()["share_token"]
    url = f"/api/shares/{token}"
    published = await require_public_share(db, token)
    assert await load_private_conversation(db, conversation_id, published.context)
    assert await load_private_conversation(db, parent.conversation_id, published.context) is None
    for suffix in ("", "/messages"):
        assert (await raw_client.get(url + suffix)).json()["messages"][0]["id"] == message_id
    assert tree.await_count == 2

    if revocation == "parent_deleted":
        await db.execute(delete(Conversation).where(Conversation.id == parent.conversation_id))
    else:
        await db.execute(
            delete(AgentSubAgentLink).where(
                AgentSubAgentLink.parent_agent_id == job.parent_agent_id,
                AgentSubAgentLink.sub_agent_id == job.child_agent_id,
            )
        )
    await db.commit()

    assert (await client.get(f"/api/conversations/{conversation_id}/messages")).status_code == 404
    assert (await client.get(f"/api/artifacts/{artifact_id}/download")).status_code == 404
    assert (await client.post(management)).status_code == 404
    assert (await client.get(management)).status_code == 404

    for cold in (False, True):
        if cold:
            share_cache.invalidate_token(token)
        for suffix in ("", "/messages"):
            response = await raw_client.get(url + suffix)
            assert response.status_code == 200
            assert response.json()["messages"][0]["content"] == "Published result"
        artifacts = (await raw_client.get(url + "/artifacts")).json()
        assert [artifact["id"] for artifact in artifacts] == [artifact_id]
        assert (await raw_client.get(artifacts[0]["download_url"])).content == b"Published bytes"
        assert tree.await_count == (4 if cold else 2)

    org = await db.get(Organization, context.org_id)
    assert org is not None
    settings_before = org.settings
    org.settings = {**(settings_before or {}), "allow_public_share_links": False}
    await db.commit()
    assert (await raw_client.get(url)).status_code == 404
    org.settings = settings_before
    await db.commit()
    assert (await raw_client.get(url)).status_code == 200

    assert (await client.delete(management)).status_code == 204
    for suffix in ("", "/messages", "/artifacts", f"/artifacts/{artifact_id}/download"):
        denied = await raw_client.get(url + suffix)
        assert denied.status_code == 404
        assert denied.json()["error"]["code"] == "SHARE_NOT_FOUND"
    assert (await client.post(management)).status_code == 404
