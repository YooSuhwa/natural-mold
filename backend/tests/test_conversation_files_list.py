"""M3/D12 — unified conversation file list (generated + attached).

``GET /api/conversations/{id}/files`` merges generated artifacts and sent
attachments into one created_at-sorted, source-tagged stream. Coexists with
``/files/{file_path:path}`` (single-file serving) without route conflict.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.user import User
from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_generated_artifact(
    conv_id: uuid.UUID, agent_id: uuid.UUID, tmp_path: Path
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir(exist_ok=True)
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="files-run-1",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(),
    )
    await recorder.prepare()
    (output_dir / "report.md").write_text("# Artifact\n\nhello", encoding="utf-8")
    await recorder.collect_after_tool_result(
        tool_name="execute_in_skill", tool_call_id="call-files"
    )


def _attachment(conv_id: uuid.UUID, *, message_id: str | None) -> MessageAttachment:
    upload_id = uuid.uuid4()
    return MessageAttachment(
        id=upload_id,
        user_id=TEST_USER_ID,
        conversation_id=conv_id,
        message_id=message_id,
        filename="photo.png",
        mime_type="image/png",
        size_bytes=5,
        storage_path=f"/tmp/{upload_id}.png",
        url=f"/api/uploads/{upload_id}",
    )


@pytest.mark.asyncio
async def test_files_endpoint_merges_generated_and_attached(
    client: AsyncClient, db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    conv_id, agent_id = await seed_artifact_conversation()
    await _seed_generated_artifact(conv_id, agent_id, tmp_path)

    att = _attachment(conv_id, message_id="user-msg-1")
    db.add(att)
    await db.commit()

    resp = await client.get(f"/api/conversations/{conv_id}/files")
    assert resp.status_code == 200
    items = resp.json()
    assert {i["source"] for i in items} == {"generated", "attached"}

    attached = next(i for i in items if i["source"] == "attached")
    assert attached["id"] == str(att.id)
    assert attached["name"] == "photo.png"
    assert attached["extension"] == "png"
    assert attached["editable"] is False
    assert attached["message_id"] == "user-msg-1"
    assert attached["preview_url"] == attached["download_url"] == f"/api/uploads/{att.id}"

    generated = next(i for i in items if i["source"] == "generated")
    assert generated["editable"] is True
    assert generated["name"] == "report.md"

    # Single created_at-sorted (newest first) stream.
    times = [i["created_at"] for i in items]
    assert times == sorted(times, reverse=True)


@pytest.mark.asyncio
async def test_files_endpoint_excludes_unsent_attachments(
    client: AsyncClient, db: AsyncSession
) -> None:
    conv_id, _agent_id = await seed_artifact_conversation()
    db.add(_attachment(conv_id, message_id=None))  # never sent → message_id NULL
    await db.commit()

    resp = await client.get(f"/api/conversations/{conv_id}/files")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_files_endpoint_other_user_is_404(client: AsyncClient, db: AsyncSession) -> None:
    other = User(id=uuid.uuid4(), email="other@test.com", name="Other")
    model = Model(provider="openai", model_name="gpt-4o-mini", display_name="GPT-4o mini")
    db.add_all([other, model])
    await db.flush()
    agent = Agent(user_id=other.id, name="Other", system_prompt="x", model_id=model.id)
    db.add(agent)
    await db.flush()
    conv = Conversation(agent_id=agent.id, title="Other")
    db.add(conv)
    await db.commit()

    resp = await client.get(f"/api/conversations/{conv.id}/files")
    assert resp.status_code == 404
