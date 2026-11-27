"""Child uploads must not outlive the owner's authorization to read the child."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.message_attachment import MessageAttachment
from app.schemas.chat_resource_context import ChatResourceReference
from app.services import chat_resource_context_sources, skill_draft_workspace
from app.services.chat.attachments import link_attachments_to_conversation
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from tests.authz.test_independent_job_authority import delegation as delegation

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


@pytest.mark.parametrize("loss", ["parent_deleted", "edge_removed"])
async def test_global_upload_and_context_reads_follow_child_authorization(
    db: AsyncSession,
    delegation: Delegation,
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loss: str,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    context, parent, _, job = delegation
    response = await client.post(
        "/api/uploads", files={"file": ("child.txt", b"child result", "text/plain")}
    )
    assert response.status_code == 201, response.text
    upload_id = UUID(response.json()["id"])
    url = f"/api/uploads/{upload_id}"
    await link_attachments_to_conversation(
        db, context=context, attachment_ids=[upload_id], conversation_id=job.child_conversation_id
    )
    await db.commit()
    scope = chat_resource_context_sources.ResourceContextScope(
        context, job.parent_agent_id, parent.conversation_id
    )
    reference = ChatResourceReference(kind="file", id=upload_id)
    source = await chat_resource_context_sources.load_file_source(db, scope, reference)
    assert source.resolved_label == "child.txt"
    assert (await client.get(url)).content == b"child result"
    assert (await client.get(f"{url}/content")).json()["text"] == "child result"

    if loss == "parent_deleted":
        await db.execute(delete(Conversation).where(Conversation.id == parent.conversation_id))
    else:
        await db.execute(
            delete(AgentSubAgentLink).where(
                AgentSubAgentLink.parent_agent_id == job.parent_agent_id,
                AgentSubAgentLink.sub_agent_id == job.child_agent_id,
            )
        )
    await db.commit()
    assert await db.get(MessageAttachment, upload_id) is not None
    assert (await client.get(url)).status_code == 404
    assert (await client.get(f"{url}/content")).status_code == 404
    with pytest.raises(ResourceContextNotFoundError):
        await chat_resource_context_sources.load_file_source(db, scope, reference)


async def test_builder_copies_only_uploads_linked_to_its_own_conversation(
    db: AsyncSession,
    delegation: Delegation,
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    context, parent, _, job = delegation
    stored: list[UUID] = []
    for name in ["attached", "child"]:
        response = await client.post(
            "/api/uploads", files={"file": (f"{name}.txt", name.encode(), "text/plain")}
        )
        assert response.status_code == 201, response.text
        stored.append(UUID(response.json()["id"]))
    await link_attachments_to_conversation(
        db, context=context, attachment_ids=stored[1:], conversation_id=job.child_conversation_id
    )
    await link_attachments_to_conversation(
        db, context=context, attachment_ids=stored, conversation_id=parent.conversation_id
    )
    await db.commit()
    workspace = skill_draft_workspace.create_workspace(uuid4())
    copied = await skill_draft_workspace.copy_conversation_attachments_to_inputs(
        db,
        storage_path=workspace,
        attachment_ids=stored,
        context=context,
        conversation_id=parent.conversation_id,
    )
    assert copied == ["inputs/attached.txt"]
    inputs = skill_draft_workspace.resolve_workspace_dir(workspace) / "inputs"
    assert [p.read_text() for p in inputs.iterdir()] == ["attached"]
