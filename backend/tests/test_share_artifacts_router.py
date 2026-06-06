from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import settings
from app.schemas.conversation import MessageResponse
from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.conftest import TEST_USER_ID, TestSession


async def _create_artifact_for_conversation(
    tmp_path: Path,
    *,
    conv_id,
    agent_id,
    assistant_msg_id: str,
    filename: str,
    content: str,
    linked_message_ids: list[str] | None = None,
) -> tuple[str, str]:
    output_dir = tmp_path / "outputs" / assistant_msg_id
    output_dir.mkdir(parents=True, exist_ok=True)
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id=assistant_msg_id,
            output_dir=output_dir,
            linked_message_ids=linked_message_ids,
        ),
        storage=LocalArtifactStorageBackend(),
    )
    await recorder.prepare()
    (output_dir / filename).write_text(content, encoding="utf-8")
    events = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-share",
    )
    return str(conv_id), str(events[0]["id"])


async def _create_artifact(
    tmp_path: Path,
    *,
    assistant_msg_id: str,
    filename: str,
    content: str,
    linked_message_ids: list[str] | None = None,
) -> tuple[str, str]:
    conv_id, agent_id = await seed_artifact_conversation()
    return await _create_artifact_for_conversation(
        tmp_path,
        conv_id=conv_id,
        agent_id=agent_id,
        assistant_msg_id=assistant_msg_id,
        filename=filename,
        content=content,
        linked_message_ids=linked_message_ids,
    )


async def _stub_public_share_messages(
    monkeypatch: pytest.MonkeyPatch,
    *,
    conversation_id: str,
    visible_message_ids: list[str],
) -> None:
    messages = [
        MessageResponse(
            id=uuid.UUID(message_id),
            conversation_id=uuid.UUID(conversation_id),
            role="assistant",
            content=f"visible message {index}",
            created_at=datetime.now(UTC),
        )
        for index, message_id in enumerate(visible_message_ids)
    ]

    async def fake_list_messages_from_checkpointer(*_args, **_kwargs):
        return messages

    monkeypatch.setattr(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        fake_list_messages_from_checkpointer,
    )


async def _create_shared_artifact(tmp_path: Path, client: AsyncClient) -> tuple[str, str, str]:
    conv_id, artifact_id = await _create_artifact(
        tmp_path,
        assistant_msg_id="share-run-1",
        filename="shared.md",
        content="# Shared\n\npublic",
    )
    share = await client.post(f"/api/conversations/{conv_id}/share")
    assert share.status_code == 200
    return share.json()["share_token"], artifact_id, conv_id


@pytest.mark.asyncio
async def test_public_share_artifacts_list_and_content(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    conv_id, agent_id = await seed_artifact_conversation()
    visible_message_id = str(uuid.uuid4())
    _, artifact_id = await _create_artifact_for_conversation(
        tmp_path,
        conv_id=conv_id,
        agent_id=agent_id,
        assistant_msg_id="share-run-1",
        filename="shared.md",
        content="# Shared\n\npublic",
        linked_message_ids=[visible_message_id],
    )
    await _stub_public_share_messages(
        monkeypatch,
        conversation_id=str(conv_id),
        visible_message_ids=[visible_message_id],
    )
    share = await client.post(f"/api/conversations/{conv_id}/share")
    assert share.status_code == 200
    token = share.json()["share_token"]

    listed = await client.get(f"/api/shares/{token}/artifacts")
    assert listed.status_code == 200
    listed_item = listed.json()[0]
    public_base_url = f"/api/shares/{token}/artifacts/{artifact_id}"
    assert listed_item["id"] == artifact_id
    assert listed_item["url"] == public_base_url
    assert listed_item["preview_url"] == f"{public_base_url}/content"
    assert listed_item["download_url"] == f"{public_base_url}/download"

    detail = await client.get(f"/api/shares/{token}/artifacts/{artifact_id}")
    assert detail.status_code == 200
    assert detail.json()["url"] == public_base_url
    assert detail.json()["preview_url"] == f"{public_base_url}/content"
    assert detail.json()["download_url"] == f"{public_base_url}/download"

    content = await client.get(f"/api/shares/{token}/artifacts/{artifact_id}/content")
    assert content.status_code == 200
    assert content.json()["text"].startswith("# Shared")


@pytest.mark.asyncio
async def test_public_share_artifacts_are_limited_to_visible_snapshot_messages(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    conv_id, agent_id = await seed_artifact_conversation()
    visible_message_id = str(uuid.uuid4())
    hidden_message_id = str(uuid.uuid4())
    _, visible_artifact_id = await _create_artifact_for_conversation(
        tmp_path,
        conv_id=conv_id,
        agent_id=agent_id,
        assistant_msg_id="visible-run",
        filename="visible.md",
        content="# Visible",
        linked_message_ids=[visible_message_id],
    )
    _, hidden_artifact_id = await _create_artifact_for_conversation(
        tmp_path,
        conv_id=conv_id,
        agent_id=agent_id,
        assistant_msg_id="hidden-run",
        filename="hidden.md",
        content="# Hidden",
        linked_message_ids=[hidden_message_id],
    )
    await _stub_public_share_messages(
        monkeypatch,
        conversation_id=str(conv_id),
        visible_message_ids=[visible_message_id],
    )
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    listed = await client.get(f"/api/shares/{token}/artifacts")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [visible_artifact_id]

    detail = await client.get(f"/api/shares/{token}/artifacts/{hidden_artifact_id}")
    content = await client.get(f"/api/shares/{token}/artifacts/{hidden_artifact_id}/content")
    download = await client.get(f"/api/shares/{token}/artifacts/{hidden_artifact_id}/download")

    assert detail.status_code == 404
    assert content.status_code == 404
    assert download.status_code == 404


@pytest.mark.asyncio
async def test_public_share_artifact_download_is_scoped_to_shared_conversation(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    token, _artifact_id, conv_id = await _create_shared_artifact(tmp_path, client)
    await _stub_public_share_messages(
        monkeypatch,
        conversation_id=conv_id,
        visible_message_ids=[],
    )
    _other_conv_id, other_artifact_id = await _create_artifact(
        tmp_path,
        assistant_msg_id="other-conversation-run",
        filename="private.md",
        content="private",
    )

    blocked = await client.get(f"/api/shares/{token}/artifacts/{other_artifact_id}/download")

    assert blocked.status_code == 404


@pytest.mark.asyncio
async def test_public_share_artifacts_respect_revoke(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="share-run-revoke",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(),
    )
    await recorder.prepare()
    (output_dir / "revoked.md").write_text("revoked", encoding="utf-8")
    await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-share",
    )
    token = (await client.post(f"/api/conversations/{conv_id}/share")).json()["share_token"]

    revoke = await client.delete(f"/api/conversations/{conv_id}/share")
    assert revoke.status_code == 204

    listed = await client.get(f"/api/shares/{token}/artifacts")
    assert listed.status_code == 404
