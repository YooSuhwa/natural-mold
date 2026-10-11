"""Real m83 artifact ingestion and HTTP content use the same persisted scope."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from app.authz.conversation_scope import resolve_conversation_actor
from app.config import settings
from app.models.conversation_artifact import ConversationArtifact
from app.models.model import Model
from app.models.user import User
from app.services.artifact_service import (
    ArtifactRuntimeContext,
    ArtifactSnapshot,
    diff_snapshots,
    ingest_changed_files,
    snapshot_output_dir,
)
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_artifact_ingestion_preserves_scope_and_http_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, other_company: bool
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "storage"))
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-artifact@example.com",
                "name": "Artifact fixture",
                "password": "native-artifact-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-artifact@example.com")
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        agent = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": "Artifact fixture",
                "system_prompt": "Fixture",
                "model_id": str(model_id),
            },
        )
        assert agent.status_code == 201, agent.text
        conversation = await stack.client.post(
            f"/api/agents/{agent.json()['id']}/conversations",
            headers=headers,
            json={"title": "Artifact fixture"},
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = UUID(conversation.json()["id"])
        output = tmp_path / "output"
        output.mkdir()
        (output / "fixture.txt").write_text("fixture", encoding="utf-8")
        snapshot = await snapshot_output_dir(output)
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-artifact@example.com"))
            assert user is not None
            context = await resolve_conversation_actor(db, conversation_id, user.id)
            await ingest_changed_files(
                db,
                context=ArtifactRuntimeContext(
                    org_context=context,
                    conversation_id=conversation_id,
                    agent_id=UUID(agent.json()["id"]),
                    assistant_msg_id="native-turn",
                    output_dir=output,
                ),
                deltas=diff_snapshots(ArtifactSnapshot(files={}), snapshot),
                storage=LocalArtifactStorageBackend(tmp_path / "storage"),
                tool_call_id=None,
            )
            await db.commit()
            artifact = await db.scalar(select(ConversationArtifact))
            assert artifact is not None
            assert (artifact.user_id, artifact.org_id, artifact.tenant_id) == (
                context.user_id,
                context.org_id,
                context.tenant_id,
            )
            artifact_id = artifact.id
        library = await stack.client.get(f"/api/conversations/{conversation_id}/artifacts")
        assert library.status_code == 200, library.text
        assert [row["id"] for row in library.json()] == [str(artifact_id)]
        content = await stack.client.get(f"/api/artifacts/{artifact_id}/content")
        assert content.status_code == 200 and content.json()["text"] == "fixture"
