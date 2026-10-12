from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryCheckpoint, ConversationHistoryEvent
from app.schemas.conversation_history import JsonValue
from app.schemas.conversation_run import ConversationRunHistoryResponse, ConversationRunResponse
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, seed_agent
from tests.conversation_history_fixture import seed_purged_history


@dataclass(frozen=True, slots=True)
class PurgedHistoryTestContext:
    client: AsyncClient
    db: AsyncSession
    storage: Path


@dataclass(frozen=True, slots=True)
class LifecycleScenario:
    lifecycle: str
    read_only: bool


@pytest.fixture
def history_http(client: AsyncClient, db: AsyncSession) -> tuple[AsyncClient, AsyncSession]:
    return client, db


@pytest.fixture
def purged_history_context(
    history_http: tuple[AsyncClient, AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> PurgedHistoryTestContext:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path))
    return PurgedHistoryTestContext(*history_http, tmp_path)


def test_history_run_dto_is_nullable_without_weakening_execution_dto() -> None:
    # Given: a retained run projection whose deleted Agent foreign key is null.
    payload = {
        "id": uuid4(),
        "conversation_id": uuid4(),
        "agent_id": None,
        "user_id": TEST_USER_ID,
        "source": "chat",
        "status": "completed",
        "is_active": False,
        "retry_count": 0,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }

    # Then: history accepts the retained reference, while execution remains strict.
    assert ConversationRunHistoryResponse.model_validate(payload).agent_id is None
    with pytest.raises(ValidationError):
        ConversationRunResponse.model_validate(payload)


@pytest.mark.asyncio
async def test_lifecycle_history_returns_complete_purged_manifest_and_recorded_rows(
    purged_history_context: PurgedHistoryTestContext,
) -> None:
    # Given: a purged, directly owned Conversation with detached historical rows.
    client = purged_history_context.client
    db = purged_history_context.db
    seeded = await seed_purged_history(db, purged_history_context.storage)
    conversation_id = seeded.conversation_id
    run_id = seeded.run_id
    artifact_id = seeded.artifact_id
    now = seeded.now

    # When: the owner reads the dedicated history surface.
    response = await client.get(f"/api/conversations/{conversation_id}/history")

    # Then: the stable envelope is agentless, complete, and uses recorded values.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation"] == {
        "id": str(conversation_id),
        "agent_id": None,
        "agent_name": "Recorded Agent",
        "lifecycle_state": "purged",
        "archived_at": now.isoformat().replace("+00:00", "Z"),
        "purged_at": now.isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "title": "Retained history",
        "created_at": body["conversation"]["created_at"],
        "updated_at": body["conversation"]["updated_at"],
    }
    assert body["messages"] == [{"type": "human", "content": "[REDACTED]"}]
    assert body["state"] == {"summary": "masked"}
    assert body["trace"] == {"references": [{"trace_id": "masked-trace"}]}
    assert body["events"][0]["events"][0]["data"]["content"] == "[REDACTED]"
    assert body["latest_run"]["agent_id"] is None
    assert body["runs"][0]["metrics"]["estimated_cost"] == 0.12345678
    assert body["usage"][0]["estimated_cost"] == 0.123456
    assert body["artifacts"][0]["agent_name"] == "Recorded Agent"
    assert body["artifacts"][0]["size_bytes"] == 17
    assert body["artifacts"][0]["sha256"] == "b" * 64
    assert body["attachments"][0]["size_bytes"] == 11
    assert body["links"]["self"] == f"/api/conversations/{conversation_id}/history"

    # When: the same owner uses every established retained-history GET.
    messages = await client.get(f"/api/conversations/{conversation_id}/messages")
    state = await client.get(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/state"
    )
    traces = await client.get(f"/api/conversations/{conversation_id}/traces")
    active_run = await client.get(f"/api/conversations/{conversation_id}/runs/active")
    run_detail = await client.get(f"/api/conversations/{conversation_id}/runs/{run_id}")
    inputs = await client.get(f"/api/conversations/{conversation_id}/run-inputs")
    files = await client.get(f"/api/conversations/{conversation_id}/files")
    artifact_detail = await client.get(
        f"/api/conversations/{conversation_id}/artifacts/{artifact_id}"
    )
    download = await client.get(
        f"/api/conversations/{conversation_id}/artifacts/{artifact_id}/download"
    )
    message_mutation = await client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "must not run"},
    )
    state_mutation = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/state",
        json={"values": {"messages": []}},
    )

    # Then: each surface remains agentless, masked, and byte/cost exact.
    assert messages.status_code == 200, messages.text
    assert messages.json()["messages"][0]["content"] == "[REDACTED]"
    assert messages.json()["latest_run"]["agent_id"] is None
    assert state.status_code == 200, state.text
    assert state.json()["values"]["summary"] == "masked"
    assert state.json()["values"]["messages"][0]["content"] == "[REDACTED]"
    assert traces.status_code == 200, traces.text
    assert traces.json()[0]["events"][0]["data"]["content"] == "[REDACTED]"
    assert active_run.status_code == 200, active_run.text
    assert active_run.json() is None
    assert run_detail.status_code == 200, run_detail.text
    assert run_detail.json()["agent_id"] is None
    assert run_detail.json()["metrics"]["estimated_cost"] == 0.12345678
    assert inputs.status_code == 200, inputs.text
    assert inputs.json()["items"][0]["revision"] == 2
    assert files.status_code == 200, files.text
    assert {item["size_bytes"] for item in files.json()} == {11, 17}
    assert artifact_detail.status_code == 200, artifact_detail.text
    assert artifact_detail.json()["agent_name"] == "Recorded Agent"
    assert download.status_code == 200, download.text
    assert download.content == b"retained-history!"
    assert message_mutation.status_code == 409, message_mutation.text
    assert state_mutation.status_code == 409, state_mutation.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_values",
    [
        {"messages": ["malformed"]},
        {"branch": ["malformed"]},
        {"branch": "malformed"},
    ],
)
async def test_purged_state_rejects_malformed_retained_checkpoint(
    purged_history_context: PurgedHistoryTestContext,
    invalid_values: dict[str, JsonValue],
) -> None:
    # Given: a complete manifest whose retained checkpoint violates its typed contract.
    scenario = purged_history_context
    seeded = await seed_purged_history(scenario.db, scenario.storage)
    await scenario.db.execute(
        update(ConversationHistoryCheckpoint)
        .where(ConversationHistoryCheckpoint.conversation_id == seeded.conversation_id)
        .values(**invalid_values)
    )
    await scenario.db.commit()

    # When: the owner reads retained protocol state.
    response = await scenario.client.get(
        f"/api/conversations/{seeded.conversation_id}/langgraph/threads/"
        f"{seeded.conversation_id}/state"
    )

    # Then: the invalid manifest fails through a typed conflict envelope.
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONVERSATION_HISTORY_INVALID"


@pytest.mark.asyncio
async def test_purged_trace_rejects_malformed_retained_event(
    purged_history_context: PurgedHistoryTestContext,
) -> None:
    # Given: a complete manifest whose retained trace event is not an event object.
    scenario = purged_history_context
    seeded = await seed_purged_history(scenario.db, scenario.storage)
    await scenario.db.execute(
        update(ConversationHistoryEvent)
        .where(ConversationHistoryEvent.conversation_id == seeded.conversation_id)
        .values(events=["malformed"])
    )
    await scenario.db.commit()

    # When: the owner reads retained traces.
    response = await scenario.client.get(f"/api/conversations/{seeded.conversation_id}/traces")

    # Then: the invalid manifest fails through a typed conflict envelope.
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONVERSATION_HISTORY_INVALID"


@pytest.mark.asyncio
async def test_agentless_history_denies_foreign_conversation(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    # Given: purged history owned by another private actor.
    conversation = Conversation(
        agent_id=None,
        user_id=uuid4(),
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot="Foreign",
        agent_lifecycle_state="purged",
        agent_purged_at=datetime.now(UTC),
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.commit()

    foreign_run_id = uuid4()
    foreign_artifact_id = uuid4()
    probes = [
        f"/api/conversations/{conversation.id}/history",
        f"/api/conversations/{conversation.id}/messages",
        f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/state",
        f"/api/conversations/{conversation.id}/traces",
        f"/api/conversations/{conversation.id}/runs/active",
        f"/api/conversations/{conversation.id}/runs/{foreign_run_id}",
        f"/api/conversations/{conversation.id}/run-inputs",
        f"/api/conversations/{conversation.id}/files",
        f"/api/conversations/{conversation.id}/artifacts/{foreign_artifact_id}",
        f"/api/conversations/{conversation.id}/artifacts/{foreign_artifact_id}/download",
    ]

    # When: the current owner probes every retained-history reader.
    responses = [await client.get(path) for path in probes]

    # Then: every boundary returns the same enumeration-safe not-found response.
    assert [response.status_code for response in responses] == [404] * len(probes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [LifecycleScenario("active", False), LifecycleScenario("archived", True)],
)
async def test_lifecycle_history_keeps_stable_live_and_archived_envelopes(
    client: AsyncClient,
    db: AsyncSession,
    scenario: LifecycleScenario,
) -> None:
    # Given: a live Agent whose current name differs from the Conversation snapshot.
    _, _, agent = await seed_agent(db, name="Renamed current agent")
    archived_at = datetime.now(UTC) if scenario.lifecycle == "archived" else None
    conversation = Conversation(
        agent_id=agent.id,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot="Recorded at creation",
        agent_lifecycle_state=scenario.lifecycle,
        agent_archived_at_snapshot=archived_at,
        title=f"{scenario.lifecycle} history",
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.commit()

    # When: the owner reads the same stable history surface.
    response = await client.get(f"/api/conversations/{conversation.id}/history")

    # Then: lifecycle and recorded identity do not depend on the mutable Agent name.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation"]["agent_id"] == str(agent.id)
    assert body["conversation"]["agent_name"] == "Recorded at creation"
    assert body["conversation"]["lifecycle_state"] == scenario.lifecycle
    assert body["conversation"]["read_only"] is scenario.read_only
    assert body["conversation"]["archived_at"] == (
        archived_at.isoformat().replace("+00:00", "Z") if archived_at is not None else None
    )
    assert body["manifest"] is None
