from __future__ import annotations

from datetime import UTC, datetime
from typing import Never
from unicodedata import normalize
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_purge import AgentPurgeReceipt
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryManifest
from app.models.conversation_run import ConversationRun
from app.models.user import User
from app.services import agent_purge_cleanup
from app.services.agent_purge_preparation import prepare_agent_purge
from app.services.agent_purge_relational import finalize_agent_purge
from app.services.agent_purge_service import purge_agent
from app.services.agent_purge_types import AgentPurgeCommand
from app.services.conversation_history_snapshot import (
    HistorySnapshotIncompleteError,
    materialize_conversation_history,
)
from app.services.conversation_history_types import ConversationHistoryMaterializationRequest
from tests.agent_purge_cleanup_cases import RecordingSaver
from tests.agent_purge_service_cases import grant_test_org_admin, seed_focused_purge_history
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession, seed_agent


async def test_owner_without_org_admin_cannot_start_purge(client: AsyncClient) -> None:
    # Given: an owner has archived an otherwise stopped Agent.
    async with TestSession() as db:
        _, _, agent = await seed_agent(db, name="Typed purge target")
        await db.commit()
        agent_id = agent.id
    archived = await client.delete(f"/api/agents/{agent_id}")
    assert archived.status_code == 204

    # When: the owner submits the exact typed name to the purge surface.
    response = await client.post(
        f"/api/agents/{agent_id}/purge",
        json={"confirmation_name": "Typed purge target"},
    )

    # Then: effective org-admin authority is required before preparation begins.
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AGENT_LIFECYCLE_PERMISSION_REQUIRED"


async def test_exact_unicode_name_purge_is_idempotent(client: AsyncClient) -> None:
    # Given: an org-admin archives a stopped Agent with a composed Unicode name.
    await grant_test_org_admin()
    name = "Caf\u00e9 purge"
    async with TestSession() as db:
        _, _, agent = await seed_agent(db, name=name)
        await db.commit()
        agent_id = agent.id
    archived = await client.delete(f"/api/agents/{agent_id}")
    assert archived.status_code == 204

    # When: canonically equivalent but codepoint-distinct text is submitted.
    mismatch = await client.post(
        f"/api/agents/{agent_id}/purge",
        json={"confirmation_name": normalize("NFD", name)},
    )

    # Then: no receipt or deletion is created for the wrong codepoint sequence.
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "AGENT_PURGE_NAME_MISMATCH"
    async with TestSession() as db:
        assert await db.get(Agent, agent_id) is not None
        assert await db.scalar(select(func.count()).select_from(AgentPurgeReceipt)) == 0

    # When: the exact name is submitted twice.
    first = await client.post(
        f"/api/agents/{agent_id}/purge",
        json={"confirmation_name": name},
    )
    deleted_name_mismatch = await client.post(
        f"/api/agents/{agent_id}/purge",
        json={"confirmation_name": normalize("NFD", name)},
    )
    replay = await client.post(
        f"/api/agents/{agent_id}/purge",
        json={"confirmation_name": name},
    )

    # Then: one completed receipt and one accurate purge audit are reused.
    assert first.status_code == 200
    assert deleted_name_mismatch.status_code == 409
    assert deleted_name_mismatch.json()["error"]["code"] == "AGENT_PURGE_NAME_MISMATCH"
    assert replay.status_code == 200
    assert first.json() == replay.json()
    async with TestSession() as db:
        assert await db.get(Agent, agent_id) is None
        assert await db.scalar(select(func.count()).select_from(AgentPurgeReceipt)) == 1
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "agent.purge")
            )
            == 1
        )


async def test_restore_conflicts_with_preparing_purge_receipt(client: AsyncClient) -> None:
    # Given: an archived Agent has committed transaction A of purge preparation.
    await grant_test_org_admin()
    async with TestSession() as db:
        _, _, agent = await seed_agent(db, name="Restore race")
        await db.commit()
        agent_id = agent.id
    archived = await client.delete(f"/api/agents/{agent_id}")
    assert archived.status_code == 204
    async with TestSession() as db:
        stored = await db.get(Agent, agent_id)
        actor = await db.get(User, TEST_USER_ID)
        assert stored is not None and actor is not None
        db.add(
            AgentPurgeReceipt(
                id=uuid4(),
                tenant_id=stored.tenant_id,
                org_id=stored.org_id,
                agent_id_snapshot=stored.id,
                owner_user_id_snapshot=stored.user_id,
                agent_name_snapshot=stored.name,
                requester_user_id=actor.id,
                state="preparing",
            )
        )
        await db.commit()

    # When: restore races after the durable preparation commit.
    restored = await client.post(f"/api/agents/{agent_id}/restore")

    # Then: the same lifecycle lock protects the archived state for transaction B.
    assert restored.status_code == 409
    assert restored.json()["error"]["code"] == "AGENT_LIFECYCLE_EXECUTION_PENDING"
    async with TestSession() as db:
        stored = await db.get(Agent, agent_id)
        assert stored is not None and stored.archived_at is not None


async def test_purge_materializes_history_before_detaching_agent(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an archived stopped Agent has retained Conversation and Run history.
    await grant_test_org_admin()
    history = await seed_focused_purge_history()
    archived = await client.delete(f"/api/agents/{history.agent_id}")
    assert archived.status_code == 204
    saver = RecordingSaver()
    monkeypatch.setattr(
        agent_purge_cleanup.checkpointer,
        "get_checkpointer",
        lambda: saver,
    )

    # When: the org-admin purges with the exact typed name.
    async with TestSession() as db:
        actor_row = await db.get(User, TEST_USER_ID)
        assert actor_row is not None
        receipt = await purge_agent(
            AgentPurgeCommand(
                db=db,
                actor=CurrentUser(
                    id=actor_row.id,
                    email=actor_row.email,
                    name=actor_row.name,
                ),
                agent_id=history.agent_id,
                confirmation_name="History purge",
                checkpointer=history.saver,
            )
        )

    # Then: a fresh session sees the retained graph detached after a complete manifest.
    assert receipt.state == "completed"
    assert saver.deleted_threads == [str(history.conversation_id)]
    async with TestSession() as db:
        retained_conversation = await db.get(Conversation, history.conversation_id)
        retained_run = await db.get(ConversationRun, history.run_id)
        manifest = await db.scalar(
            select(ConversationHistoryManifest).where(
                ConversationHistoryManifest.conversation_id == history.conversation_id
            )
        )
        assert await db.get(Agent, history.agent_id) is None
        assert retained_conversation is not None
        assert (
            retained_conversation.agent_id,
            retained_conversation.agent_lifecycle_state,
            retained_conversation.agent_name_snapshot,
            retained_conversation.title,
        ) == (None, "purged", "History purge", "retained private title")
        assert retained_run is not None and retained_run.agent_id is None
        assert manifest is not None and manifest.state == "complete"


async def test_purge_aborts_when_any_conversation_manifest_is_incomplete(
    client: AsyncClient,
) -> None:
    # Given: transaction A covers two conversations but only one snapshot is complete.
    await grant_test_org_admin()
    history = await seed_focused_purge_history()
    async with TestSession() as db:
        user = await db.get(User, TEST_USER_ID)
        assert user is not None
        db.add(
            Conversation(
                agent_id=history.agent_id,
                user_id=user.id,
                tenant_id=TEST_TENANT_ID,
                org_id=TEST_ORG_ID,
                agent_name_snapshot="History purge",
                last_activity_at=datetime.now(UTC),
            )
        )
        await db.commit()
    archived = await client.delete(f"/api/agents/{history.agent_id}")
    assert archived.status_code == 204
    async with TestSession() as db:
        actor = await db.get(User, TEST_USER_ID)
        assert actor is not None
        command = AgentPurgeCommand(
            db,
            CurrentUser(id=actor.id, email=actor.email, name=actor.name),
            history.agent_id,
            "History purge",
            history.saver,
        )
        prepared = await prepare_agent_purge(command)
        await db.commit()
        await materialize_conversation_history(
            ConversationHistoryMaterializationRequest(
                db,
                command.actor,
                command.agent_id,
                history.conversation_id,
                command.confirmation_name,
                history.saver,
            )
        )
        await db.commit()

        # When: transaction B uses metadata-only governance SQL and revalidates membership.
        statements: list[str] = []

        def capture_statement(*, statement: str, **_: Never) -> None:
            statements.append(statement)

        connection = await db.connection()
        event.listen(
            connection.sync_connection, "before_cursor_execute", capture_statement, named=True
        )
        try:
            with pytest.raises(HistorySnapshotIncompleteError):
                await finalize_agent_purge(command, prepared)
        finally:
            event.remove(connection.sync_connection, "before_cursor_execute", capture_statement)
        await db.rollback()
        agent_selects = "\n".join(
            statement.lower()
            for statement in statements
            if "select" in statement.lower() and "agents" in statement.lower()
        )
        assert "system_prompt" not in agent_selects
        assert "model_params" not in agent_selects

    # Then: no relational deletion or external-cleanup staging occurred.
    async with TestSession() as db:
        assert await db.get(Agent, history.agent_id) is not None
        receipt = await db.scalar(select(AgentPurgeReceipt))
        assert receipt is not None and receipt.state == "preparing"
