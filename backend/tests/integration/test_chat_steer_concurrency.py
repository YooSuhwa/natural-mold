"""PostgreSQL contracts for same-run instructions, using the real authenticated routes."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime import checkpointer
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token, create_csrf_token
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_steer import ConversationRunSteer
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.services import conversation_run_service
from app.services.conversation_run_steer_rollover import reconcile_terminal_steers
from tests.authz.unit_scope_fixture import TEST_ORG_ID, unit_context
from tests.integration.test_conversation_queue_concurrency import (
    _postgres_session_factory,
    _seed_conversation,
)

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ActiveChat:
    sessions: async_sessionmaker[AsyncSession]
    client: AsyncClient
    user_id: UUID
    conversation_id: UUID
    run_id: UUID

    @property
    def receipt_url(self) -> str:
        return f"/api/conversations/{self.conversation_id}/runs/{self.run_id}/steers"

    async def send(self, key: UUID, text: str) -> Response:
        return await self.client.post(
            self.receipt_url, json={"text": text, "client_request_id": str(key)}
        )


@pytest.fixture
async def active_chat(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ActiveChat]:
    sessions, engine = _postgres_session_factory()
    try:
        user_id, agent_id, conversation_id = await _seed_conversation(sessions)
        async with sessions() as db:
            run = await conversation_run_service.create_run(
                db,
                conversation_id=conversation_id,
                agent_id=agent_id,
                context=unit_context(user_id),
                source="chat",
                input_preview="compare results",
            )
            await conversation_run_service.transition_run(
                db, run, "running", worker_instance_id="test"
            )
            await db.commit()
        app = create_app()

        async def isolated_db() -> AsyncIterator[AsyncSession]:
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db] = isolated_db
        monkeypatch.setattr(settings, "chat_steer_enabled", True)
        csrf = create_csrf_token(user_id)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-CSRF-Token": csrf},
            cookies={
                settings.cookie_name_access: create_access_token(user_id),
                settings.cookie_name_csrf: csrf,
                ACTIVE_ORG_COOKIE: str(TEST_ORG_ID),
            },
        ) as client:
            yield ActiveChat(sessions, client, user_id, conversation_id, run.id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_admissions_keep_one_receipt_per_request(active_chat: ActiveChat) -> None:
    first, second, third = (uuid4() for _ in range(3))
    requests = [(first, "first"), (first, "first"), (second, "second"), (third, "third")]
    async with asyncio.timeout(30):
        responses = await asyncio.gather(*(active_chat.send(key, text) for key, text in requests))
    assert [response.status_code for response in responses] == [200] * len(requests)
    assert responses[0].json() == responses[1].json()
    assert len({response.json()["id"] for response in responses}) == 3

    conflict = await active_chat.send(first, "different directive")
    assert conflict.status_code == 409
    receipts = await active_chat.client.get(active_chat.receipt_url)
    assert receipts.status_code == 200
    assert [item["sequence"] for item in receipts.json()] == [1, 2, 3]
    assert {item["run_id"] for item in receipts.json()} == {str(active_chat.run_id)}


@pytest.mark.asyncio
async def test_other_tenant_of_same_user_cannot_read_or_submit(active_chat: ActiveChat) -> None:
    key = uuid4()
    assert (await active_chat.send(key, "owner-only directive")).status_code == 200
    async with active_chat.sessions() as db:
        suffix = uuid4().hex
        tenant = Tenant(name="Another company", slug=suffix, secrets_namespace=suffix)
        db.add(tenant)
        await db.flush()
        org = Organization(tenant_id=tenant.id, name="Another organization", slug=suffix)
        db.add(org)
        await db.flush()
        db.add(TenantMember(tenant_id=tenant.id, user_id=active_chat.user_id))
        db.add(OrganizationMember(tenant_id=tenant.id, org_id=org.id, user_id=active_chat.user_id))
        await db.commit()
    active_chat.client.cookies.set(ACTIVE_ORG_COOKIE, str(org.id))
    for method in ("get", "post"):
        body = (
            {"text": "owner-only directive", "client_request_id": str(key)}
            if method == "post"
            else None
        )
        foreign = await active_chat.client.request(method, active_chat.receipt_url, json=body)
        missing = await active_chat.client.request(
            method,
            f"/api/conversations/{uuid4()}/runs/{uuid4()}/steers",
            json=body,
        )
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()


@pytest.mark.asyncio
async def test_terminal_race_and_repeated_recovery_enqueue_each_accepted_input_once(
    active_chat: ActiveChat,
) -> None:
    first_key, racing_key = uuid4(), uuid4()
    first = await active_chat.send(first_key, "first directive")
    assert first.status_code == 200

    async def finish() -> None:
        async with active_chat.sessions() as db:
            run = await conversation_run_service.get_run_for_user(
                db,
                conversation_id=active_chat.conversation_id,
                run_id=active_chat.run_id,
                context=unit_context(active_chat.user_id),
                for_update=True,
            )
            assert run is not None
            await conversation_run_service.transition_run(db, run, "completed")
            await db.commit()

    async def reconcile() -> None:
        async with active_chat.sessions() as db:
            await reconcile_terminal_steers(
                db,
                conversation_id=active_chat.conversation_id,
                run_id=active_chat.run_id,
                context=unit_context(active_chat.user_id),
            )
            await db.commit()

    async with asyncio.timeout(30):
        racing, _ = await asyncio.gather(active_chat.send(racing_key, "racing directive"), finish())
        assert racing.status_code in (200, 409)
        await asyncio.gather(reconcile(), reconcile())
    duplicate = await active_chat.send(first_key, "first directive")
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    assert (await active_chat.send(uuid4(), "too late")).status_code == 409
    accepted_keys = {first_key, racing_key} if racing.status_code == 200 else {first_key}
    async with active_chat.sessions() as db:
        receipts = list(
            (
                await db.scalars(
                    select(ConversationRunSteer).where(
                        ConversationRunSteer.run_id == active_chat.run_id
                    )
                )
            ).all()
        )
        queue = list(
            (
                await db.scalars(
                    select(ConversationRunInput)
                    .where(ConversationRunInput.conversation_id == active_chat.conversation_id)
                    .order_by(ConversationRunInput.position)
                )
            ).all()
        )
    assert {row.client_request_id for row in receipts} == accepted_keys
    assert {row.status for row in receipts} == {"queued"}
    assert [row.id for row in queue] == [
        row.queued_input_id for row in sorted(receipts, key=lambda row: row.sequence)
    ]
    assert all(row.status == "pending" for row in queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before", "after"])
async def test_new_worker_confirms_only_steers_observed_by_model_after_worker_crash(
    active_chat: ActiveChat,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    context = unit_context(active_chat.user_id)
    submitted = await active_chat.send(uuid4(), "use domestic sources")
    assert submitted.status_code == 200
    receipt_id = submitted.json()["id"]
    url = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    worker_env = {
        key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ
    }
    worker_env.update(
        DATABASE_URL=url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
        DATABASE_URL_SYNC=dsn,
        ENCRYPTION_KEYS=secrets.token_hex(32),
        CHAT_STEER_ENABLED="true",
        MOLDY_DISABLE_ENV_FILE="true",
        PYTHON_DOTENV_DISABLED="1",
    )
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.integration.chat_steer_checkpoint_worker",
        str(active_chat.conversation_id),
        str(active_chat.run_id),
        str(context.tenant_id),
        boundary,
        cwd=BACKEND_ROOT,
        env=worker_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), 90)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    assert child.returncode == 75, stderr.decode()
    observed = json.loads(stdout.decode().splitlines()[-1])
    if boundary == "before":
        assert observed == {"calls": [], "staged": [receipt_id], "confirmed": []}
    else:
        assert observed == {
            "calls": [["compare sources", "use domestic sources"]],
            "staged": [],
            "confirmed": [receipt_id],
        }

    async with active_chat.sessions() as db:
        row = await db.get(ConversationRunSteer, UUID(receipt_id))
        assert row is not None and row.delivery_started_at is not None
        assert row.status == "pending" and row.applied_at is None
        terminal = await conversation_run_service.get_run_for_user(
            db,
            conversation_id=active_chat.conversation_id,
            run_id=active_chat.run_id,
            context=context,
            for_update=True,
        )
        assert terminal is not None
        await conversation_run_service.transition_run(db, terminal, "stale")
        await db.commit()
    monkeypatch.setattr(settings, "chat_steer_enabled", False)
    async with AsyncPostgresSaver.from_conn_string(dsn) as reader:
        monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: reader)
        async with active_chat.sessions() as db:
            await reconcile_terminal_steers(
                db,
                conversation_id=active_chat.conversation_id,
                run_id=active_chat.run_id,
                context=context,
            )
            await db.commit()

    recovered = await active_chat.client.get(active_chat.receipt_url)
    assert recovered.status_code == 200
    [receipt] = recovered.json()
    assert receipt["status"] == ("applied" if boundary == "after" else "needs_review")
    assert (receipt["applied_at"] is not None) == (boundary == "after")
    assert receipt["queued_input_id"] is None
