from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import require_conversation_agent_id
from app.config import settings
from app.models.conversation import Conversation
from app.services import conversation_run_service
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, register_session
from tests.integration._seed import seed_conversation_with_agent


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chat_steer_enabled", True)


async def active_run(db: AsyncSession) -> tuple[UUID, UUID]:
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conversation),
        context=unit_context(TEST_USER_ID),
        source="chat",
        input_preview="inspect the sources",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="worker")
    await db.commit()
    return conversation_id, run.id


@pytest.mark.usefixtures("enabled")
async def test_receipt_replays_after_disconnect_disable_and_retry(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id, run_id = await active_run(db)
    path = f"/api/conversations/{conversation_id}/runs/{run_id}/steers"
    request = {"client_request_id": str(uuid4()), "text": "국내 사례만 조사해 줘"}
    first = await client.post(path, json=request)
    assert first.status_code == 200, first.text
    receipt = first.json()
    assert receipt["run_id"] == str(run_id) and receipt["status"] == "pending"
    assert receipt["text"] == request["text"] and receipt["applied_at"] is None
    assert (await client.post(path, json=request)).json() == receipt
    assert (await client.get(path)).json() == [receipt]

    monkeypatch.setattr(settings, "chat_steer_enabled", False)
    assert (
        await client.post(path, json={**request, "client_request_id": str(uuid4())})
    ).status_code == 404
    recovered = await client.get(f"/api/conversations/{conversation_id}/steers")
    assert recovered.json() == [receipt]
    active = await conversation_run_service.get_active_run(
        db, conversation_id=conversation_id, context=unit_context(TEST_USER_ID)
    )
    assert active is not None and active.id == run_id and active.cancel_requested_at is None


@pytest.mark.usefixtures("enabled")
async def test_steer_refuses_a_run_from_another_conversation(
    client: AsyncClient, db: AsyncSession
) -> None:
    conversation_id, _ = await active_run(db)
    _, other_run_id = await active_run(db)
    root = f"/api/conversations/{conversation_id}/runs"
    request = {"client_request_id": str(uuid4()), "text": "foreign scope probe"}
    foreign = f"{root}/{other_run_id}/steers"
    missing = f"{root}/{uuid4()}/steers"
    for method in ("get", "post"):
        kwargs = {"json": request} if method == "post" else {}
        existing = await client.request(method, foreign, **kwargs)
        absent = await client.request(method, missing, **kwargs)
        assert existing.status_code == absent.status_code == 404
        assert existing.json() == absent.json()
    assert (await client.get(f"/api/conversations/{conversation_id}/steers")).json() == []


@pytest.mark.usefixtures("enabled")
async def test_steer_input_contract_limits_content_and_ids(
    client: AsyncClient, db: AsyncSession
) -> None:
    conversation_id, run_id = await active_run(db)
    path = f"/api/conversations/{conversation_id}/runs/{run_id}/steers"
    valid = {"client_request_id": str(uuid4()), "text": "add sources"}
    for payload in (
        {**valid, "text": "  "},
        {**valid, "text": "x" * 12_001},
        {**valid, "client_request_id": "not-a-uuid"},
        {**valid, "attachments": ["file"]},
    ):
        assert (await client.post(path, json=payload)).status_code == 422
    assert (await client.get(path)).json() == []


@pytest.mark.usefixtures("enabled")
async def test_steer_requires_authenticated_actor_and_csrf(raw_client: AsyncClient) -> None:
    path = f"/api/conversations/{uuid4()}/runs/{uuid4()}/steers"
    payload = {"client_request_id": str(uuid4()), "text": "inspect"}
    assert (await raw_client.get(path)).status_code == 401
    session = await register_session(raw_client, email="steer-auth@test.com", name="Steer Test")
    session.apply(raw_client)
    assert (await raw_client.post(path, json=payload)).status_code == 403
    assert (await raw_client.post(path, headers=session.headers(), json=payload)).status_code == 404
