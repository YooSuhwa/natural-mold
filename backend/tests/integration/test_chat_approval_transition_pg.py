"""An immediate explicit approval cannot hold the locks needed to commit its own interrupt."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.event import listens_for
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.agent_runtime.langgraph_event_delivery import ProtocolEventDelivery
from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token, create_csrf_token
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import ShieldedAsyncSession, tenant_session
from app.dependencies import get_db
from app.main import create_app
from app.models import (
    Agent,
    Conversation,
    ConversationRun,
    Model,
    Organization,
    OrganizationMember,
    TenantMember,
    User,
)
from app.services import audit_service, conversation_run_worker, trace_storage
from app.services.conversation_run_transitions import _transition  # noqa: PLC2701
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@dataclass(frozen=True)
class Turn:
    actor: OrgContext
    room: UUID
    run: UUID
    interrupt: str


def _seed(lab: RlsLab) -> Turn:
    tenant = lab.tenant_a
    with Session(lab.admin) as db:
        account = User(name="Owner", email=f"{uuid4().hex}@test.invalid")
        organization = Organization(tenant_id=tenant, name="Approval org", slug=uuid4().hex)
        model = Model(provider="openai", model_name="gpt-4o", display_name="Test")
        db.add_all([account, organization, model])
        db.flush()
        context = OrgContext(organization.id, tenant, account.id)
        scope = {"user_id": account.id, "org_id": organization.id, "tenant_id": tenant}
        assistant = Agent(**scope, name="Assistant", system_prompt="test", model_id=model.id)
        db.add_all(
            [
                assistant,
                TenantMember(tenant_id=tenant, user_id=account.id),
                OrganizationMember(**scope),
            ]
        )
        db.flush()
        room = Conversation(
            **scope,
            agent_id=assistant.id,
            agent_name_snapshot=assistant.name,
            last_activity_at=datetime.now(UTC),
        )
        db.add(room)
        db.flush()
        run = ConversationRun(
            **scope,
            conversation_id=room.id,
            agent_id=assistant.id,
            source="chat",
            status="running",
            is_active=True,
        )
        db.add(run)
        db.flush()
        turn = Turn(context, room.id, run.id, uuid4().hex)
        db.commit()
        return turn


async def _publish(turn: Turn, db: AsyncSession) -> None:
    async def persist(events: list[dict[str, Any]]) -> None:
        await trace_storage.append_events(
            db, conversation_id=turn.room, assistant_msg_id=str(turn.run), events_chunk=events
        )
        await db.commit()

    delivery = ProtocolEventDelivery(run_id=str(turn.run), persist_callback=persist)
    wire = await delivery.emit(
        stored_protocol_event(
            run_id=str(turn.run),
            thread_id=str(turn.room),
            seq=1,
            method="input.requested",
            namespace=[],
            data={
                "interrupt_id": turn.interrupt,
                "payload": {
                    "action_requests": [
                        {"name": "execute_in_skill", "args": {"command": "make report"}}
                    ],
                    "review_configs": [
                        {
                            "action_name": "execute_in_skill",
                            "allowed_decisions": ["approve", "reject"],
                        }
                    ],
                },
            },
        )
    )
    assert "input.requested" in wire and turn.interrupt in wire
    await delivery.close()


def _revoke(lab: RlsLab, actor: OrgContext) -> None:
    with lab.admin.begin() as db:
        db.execute(sa.text("SET LOCAL lock_timeout = '1s'"))
        db.execute(
            sa.update(OrganizationMember)
            .where(
                OrganizationMember.org_id == actor.org_id,
                OrganizationMember.user_id == actor.user_id,
            )
            .values(status="removed")
        )


@pytest.mark.parametrize("case", ["single", "two-tabs", "revoked"])
async def test_public_approval_during_worker_interrupt_commit(
    lab: RlsLab, monkeypatch: pytest.MonkeyPatch, case: Literal["single", "two-tabs", "revoked"]
) -> None:
    turn = _seed(lab)
    engine = create_async_engine(async_url(lab.app_url))
    sessions = async_sessionmaker(engine, class_=ShieldedAsyncSession, expire_on_commit=False)
    worker_engine = create_async_engine(
        async_url(lab.app_url),
        connect_args={"server_settings": {"application_name": "approval_transition_worker"}},
    )
    worker_sessions = async_sessionmaker(
        worker_engine, class_=ShieldedAsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(conversation_run_worker, "async_session", worker_sessions)
    monkeypatch.setattr(audit_service, "async_session", sessions)
    app = create_app()
    all_waiting = asyncio.Event()
    attempts = 2 if case == "two-tabs" else 1
    request_pid: ContextVar[int | None] = ContextVar("approval_request_pid", default=None)
    waiters: set[int] = set()
    starts: list[UUID] = []

    async def database() -> AsyncIterator[AsyncSession]:
        async with sessions() as db:
            pid = await db.scalar(sa.text("SELECT pg_backend_pid()"))
            token = request_pid.set(pid)
            try:
                yield db
            finally:
                request_pid.reset(token)

    @listens_for(engine.sync_engine, "after_cursor_execute")
    def observe_running_parent(
        _conn: Any, _cursor: Any, statement: str, _params: Any, _context: Any, _many: bool
    ) -> None:
        # Observe the real owned active-run lookup, leaving the worker independent.
        pid = request_pid.get()
        if pid is not None and "conversation_runs.is_active is true" in statement.casefold():
            waiters.add(pid)
            if len(waiters) == attempts:
                all_waiting.set()

    async def start(*, run_id: UUID, **_kwargs: Any) -> None:
        # Admission and its returned child are real. Stop at the external execution boundary.
        starts.append(run_id)

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr("app.routers.conversation_agent_protocol.start_conversation_run", start)
    try:
        async with tenant_session(turn.actor, maker=sessions) as writer:
            await _publish(turn, writer)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            owner = turn.actor.user_id
            csrf = create_csrf_token(owner)
            client.cookies.set(settings.cookie_name_access, create_access_token(owner))
            client.cookies.set(settings.cookie_name_csrf, csrf)
            client.cookies.set(ACTIVE_ORG_COOKIE, str(turn.actor.org_id))
            client.headers["X-CSRF-Token"] = csrf
            url = f"/api/conversations/{turn.room}/langgraph/threads/{turn.room}/commands"

            async def approve() -> httpx.Response:
                return await client.post(
                    url,
                    json={
                        "id": uuid4().hex,
                        "method": "input.respond",
                        "params": {
                            "namespace": [],
                            "interrupt_id": turn.interrupt,
                            "response": {"decisions": [{"type": "approve"}]},
                        },
                    },
                )

            async with asyncio.timeout(30), asyncio.TaskGroup() as clients:
                submissions = [clients.create_task(approve()) for _ in range(attempts)]
                await asyncio.wait_for(all_waiting.wait(), 12)
                if case == "revoked":
                    await asyncio.to_thread(_revoke, lab, turn.actor)
                worker = clients.create_task(
                    _transition(
                        turn.run,
                        "interrupted",
                        tenant=turn.actor,
                        interrupt_id=turn.interrupt,
                        model_name="test",
                        run_metrics=RunMetricsAccumulator(started_at=None),
                        terminal_event_for_status=lambda status: dict(
                            stored_protocol_event(
                                run_id=str(turn.run),
                                thread_id=str(turn.room),
                                seq=2,
                                method="lifecycle",
                                namespace=[],
                                data={"event": status},
                            )
                        ),
                    )
                )
                with lab.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as admin:
                    while not worker.done():
                        blocked = (
                            admin.execute(
                                sa.text(
                                    "SELECT split_part(query, ' VALUES', 1) FROM pg_stat_activity "
                                    "WHERE application_name='approval_transition_worker' "
                                    "AND pg_blocking_pids(pid) && CAST(:pids AS integer[])"
                                ),
                                {"pids": sorted(waiters)},
                            )
                            .scalars()
                            .all()
                        )
                        assert not blocked, f"Approval blocked its own worker: {blocked}"
                        await asyncio.sleep(0.025)
            assert worker.result()[1] == "interrupted"
            replies = [submission.result() for submission in submissions]
            with lab.admin.connect() as admin:
                children = list(
                    admin.execute(
                        sa.select(ConversationRun.id).where(
                            ConversationRun.parent_run_id == turn.run
                        )
                    ).scalars()
                )
            if case == "revoked":
                assert replies[0].status_code in (403, 404)
                assert not starts and not children
            else:
                success = [r for r in replies if "result" in r.json()]
                assert len(success) == 1
                (accepted,) = success
                assert accepted.status_code == 200
                assert accepted.headers["x-run-id"] == accepted.json()["result"]["run_id"]
                assert children == starts == [UUID(accepted.headers["x-run-id"])]
                if case == "two-tabs":
                    (lost,) = [r for r in replies if r is not accepted]
                    assert lost.status_code in (200, 409)
                    assert lost.json()["error"]["code"] in {
                        "RESUME_NOT_FOUND",
                        "MULTITASK_REJECTED",
                        "STALE_INTERRUPT",
                    }
                    assert "x-run-id" not in lost.headers
    finally:
        await worker_engine.dispose()
        await engine.dispose()
