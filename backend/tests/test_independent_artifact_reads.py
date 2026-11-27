"""The file library honors the same live grant and run lineage as its independent chat."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_artifact import ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.schemas.chat_resource_context import ChatResourceReference
from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import get_artifact_storage_backend
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from app.services.chat_resource_context_sources import (
    ArtifactSourceRequest,
    ResourceContextScope,
    load_artifact_source,
)
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio

type Delegation = tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob]


async def _record_file(
    directory: Path,
    *,
    context: OrgContext,
    conversation_id: UUID,
    agent_id: UUID,
    turn: str,
    name: str,
) -> str:
    await asyncio.to_thread(directory.mkdir, parents=True)
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(conversation_id, context, agent_id, turn, directory),
    )
    await recorder.prepare()
    await asyncio.to_thread((directory / name).write_text, name, encoding="utf-8")
    (event,) = await recorder.collect_after_tool_result(tool_name="write_file", tool_call_id="tool")
    return str(event["id"])


async def _assert_hidden(client: AsyncClient, artifact_id: str) -> None:
    item = f"/api/artifacts/{artifact_id}"
    assert (await client.get(f"{item}/content")).status_code == 404
    assert (await client.get(f"{item}/download")).status_code == 404
    assert (await client.post(f"{item}/opened")).status_code == 404
    assert (await client.patch(item, json={"is_favorite": True})).status_code == 404


@pytest.mark.parametrize("loss", ["parent_deleted", "delegation_removed"])
async def test_artifact_reads_follow_job_authority_and_run_lineage(
    db: AsyncSession,
    delegation: Delegation,
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loss: str,
) -> None:
    context, parent_run, child_run, job = delegation
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "stored"))
    ordinary_chat, ordinary_agent = await seed_artifact_conversation()
    ordinary = await _record_file(
        tmp_path / "ordinary",
        context=context,
        conversation_id=ordinary_chat,
        agent_id=ordinary_agent,
        turn="legacy-turn",
        name="old.txt",
    )
    child_id, agent_id = job.child_conversation_id, job.child_agent_id
    result = await _record_file(
        tmp_path / "child",
        context=context,
        conversation_id=child_id,
        agent_id=agent_id,
        turn=str(child_run.id),
        name="result.md",
    )
    foreign = await _record_file(
        tmp_path / "other",
        context=context,
        conversation_id=child_id,
        agent_id=agent_id,
        turn=str(child_run.id),
        name="off-lineage.csv",
    )
    # A canonical run UUID alone is insufficient: this one belongs to the parent.
    foreign_row = await db.get(ConversationArtifact, UUID(foreign))
    assert foreign_row is not None
    foreign_row.assistant_msg_id = str(parent_run.id)
    await db.commit()

    page = (await client.get("/api/artifacts", params={"limit": 1})).json()
    assert [item["id"] for item in page["items"]] == [result]
    assert page["has_more"] and page["next_cursor"]
    next_page = (
        await client.get("/api/artifacts", params={"limit": 1, "cursor": page["next_cursor"]})
    ).json()
    assert [item["id"] for item in next_page["items"]] == [ordinary]
    assert not next_page["has_more"]
    child_library = f"/api/conversations/{child_id}/artifacts"
    assert [item["id"] for item in (await client.get(child_library)).json()] == [result]
    assert (await client.get(f"{child_library}/{foreign}/content")).status_code == 404
    await _assert_hidden(client, foreign)
    result_path = f"/api/artifacts/{result}"
    assert (await client.get(f"{result_path}/content")).json()["text"] == "result.md"
    assert (await client.get(f"{result_path}/download")).content == b"result.md"
    # Referencing a frozen artifact from another chat must revalidate the
    # original result and its exact version before accessing its storage path.
    scope = ResourceContextScope(context, ordinary_agent, ordinary_chat)
    version_id = UUID(page["items"][0]["version_id"])
    pin = ArtifactSourceRequest(
        ChatResourceReference(kind="artifact", id=UUID(result), version_id=version_id),
        get_artifact_storage_backend(),
    )
    assert (await load_artifact_source(db, scope, pin)).reference.version_id == version_id
    assert (await client.post(f"{result_path}/opened")).json()["preview_count"] == 1
    assert (await client.patch(result_path, json={"is_favorite": True})).status_code == 200
    before = (await client.get("/api/artifacts/stats")).json()
    assert (before["total_count"], before["total_size_bytes"], before["favorite_count"]) == (
        2,
        len(b"result.mdold.txt"),
        1,
    )

    if loss == "parent_deleted":
        await db.execute(delete(Conversation).where(Conversation.id == parent_run.conversation_id))
    else:
        edge = await db.get(AgentSubAgentLink, (job.parent_agent_id, agent_id))
        assert edge is not None
        await db.delete(edge)
    await db.commit()

    page = (await client.get("/api/artifacts", params={"limit": 1})).json()
    assert [item["id"] for item in page["items"]] == [ordinary]
    assert page["next_cursor"] is None and not page["has_more"]
    assert [item["id"] for item in (await client.get("/api/artifacts/recent")).json()] == [ordinary]
    after = (await client.get("/api/artifacts/stats")).json()
    assert (after["total_count"], after["total_size_bytes"], after["favorite_count"]) == (
        1,
        len(b"old.txt"),
        0,
    )
    assert after["recent_count_7d"] == 1
    assert len(after["by_kind"]) == 1
    assert after["by_kind"][0]["size_bytes"] == len(b"old.txt")
    with pytest.raises(ResourceContextNotFoundError):
        await load_artifact_source(db, scope, pin)
    await _assert_hidden(client, result)
    assert (await client.get(f"/api/artifacts/{ordinary}/download")).content == b"old.txt"
