from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import settings
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.conftest import TEST_USER_ID, TestSession


async def _create_artifact(tmp_path: Path, *, name: str = "report.md") -> tuple[str, str]:
    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="router-run-1",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(),
    )
    await recorder.prepare()
    (output_dir / name).write_text("# Artifact\n\nhello", encoding="utf-8")
    events = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-router",
    )
    return str(conv_id), str(events[0]["id"])


@pytest.mark.asyncio
async def test_conversation_artifact_content_and_download(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    conv_id, artifact_id = await _create_artifact(tmp_path)

    listed = await client.get(f"/api/conversations/{conv_id}/artifacts")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == artifact_id
    assert listed.json()[0]["path"] == "report.md"

    content = await client.get(f"/api/conversations/{conv_id}/artifacts/{artifact_id}/content")
    assert content.status_code == 200
    assert content.json()["text"].startswith("# Artifact")
    assert content.json()["truncated"] is False

    downloaded = await client.get(
        f"/api/conversations/{conv_id}/artifacts/{artifact_id}/download"
    )
    assert downloaded.status_code == 200
    assert downloaded.text.startswith("# Artifact")
    assert "report.md" in downloaded.headers["content-disposition"]


@pytest.mark.asyncio
async def test_artifact_library_filters_favorite_opened_and_stats(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    _, artifact_id = await _create_artifact(tmp_path, name="chart.csv")

    page = await client.get("/api/artifacts", params={"q": "chart", "kind": "data"})
    assert page.status_code == 200
    assert page.json()["items"][0]["id"] == artifact_id

    favorite = await client.patch(f"/api/artifacts/{artifact_id}", json={"is_favorite": True})
    assert favorite.status_code == 200
    assert favorite.json()["is_favorite"] is True

    opened = await client.post(f"/api/artifacts/{artifact_id}/opened")
    assert opened.status_code == 200
    assert opened.json()["preview_count"] == 1

    stats = await client.get("/api/artifacts/stats")
    assert stats.status_code == 200
    assert stats.json()["total_count"] == 1
    assert stats.json()["favorite_count"] == 1
    assert stats.json()["by_kind"][0]["kind"] == "data"


@pytest.mark.asyncio
async def test_conversation_artifact_list_hides_other_user_conversation(
    client: AsyncClient,
    db,
) -> None:
    other_user = User(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email="other@test.com",
        name="Other",
    )
    model = Model(provider="openai", model_name="gpt-4o-mini", display_name="GPT-4o mini")
    db.add_all([other_user, model])
    await db.flush()
    agent = Agent(
        user_id=other_user.id,
        name="Other agent",
        description=None,
        system_prompt="Other",
        model_id=model.id,
        status="active",
    )
    db.add(agent)
    await db.flush()
    conv = Conversation(agent_id=agent.id, title="Other conversation")
    db.add(conv)
    await db.commit()

    response = await client.get(f"/api/conversations/{conv.id}/artifacts")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_conversation_file_endpoint_hides_other_user_file(
    client: AsyncClient,
    db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "conversation_output_dir", str(tmp_path / "conversations"))
    other_user = User(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        email="other-file@test.com",
        name="Other File",
    )
    model = Model(provider="openai", model_name="gpt-4o-mini", display_name="GPT-4o mini")
    db.add_all([other_user, model])
    await db.flush()
    agent = Agent(
        user_id=other_user.id,
        name="Other file agent",
        description=None,
        system_prompt="Other",
        model_id=model.id,
        status="active",
    )
    db.add(agent)
    await db.flush()
    conv = Conversation(agent_id=agent.id, title="Other file conversation")
    db.add(conv)
    await db.commit()
    output_dir = tmp_path / "conversations" / str(conv.id)
    output_dir.mkdir(parents=True)
    (output_dir / "secret.txt").write_text("secret", encoding="utf-8")

    response = await client.get(f"/api/conversations/{conv.id}/files/secret.txt")

    assert response.status_code == 404
