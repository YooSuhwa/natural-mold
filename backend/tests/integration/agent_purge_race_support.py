"""Real PostgreSQL same-request race orchestration for Agent purge."""

from __future__ import annotations

from contextlib import AsyncExitStack
from contextvars import ContextVar
from typing import Literal
from uuid import UUID

import pytest
from anyio import Event, create_task_group, fail_after
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_history import ConversationHistoryManifest
from app.services import agent_purge_preparation, agent_purge_service
from app.services.agent_purge_types import AgentPurgeCommand, PreparedAgentPurge
from app.services.conversation_history_types import ConversationHistoryMaterializationRequest
from tests.authz.agent_lifecycle_fixture import RegisteredLifecycleFixture
from tests.authz.agent_purge_native_support import actor_client

type NativePurgeRacePhase = Literal["prepare", "snapshot", "finalize"]


class PurgeRaceGate:
    """Coordinate one first-call barrier per request for a two-request race."""

    __slots__ = (
        "_arrivals",
        "_finished",
        "_leader_index",
        "_phase_seen",
        "_request_index",
        "_second_arrived",
    )

    def __init__(self) -> None:
        self._second_arrived = Event()
        self._finished = (Event(), Event())
        self._request_index: ContextVar[int] = ContextVar("purge_request_index")
        self._phase_seen: ContextVar[bool] = ContextVar("purge_phase_seen", default=False)
        self._arrivals = 0
        self._leader_index = 0

    def bind(self, index: int) -> None:
        self._request_index.set(index)

    def finish(self, index: int) -> None:
        self._finished[index].set()

    async def wait(self) -> None:
        if self._phase_seen.get():
            return
        self._phase_seen.set(True)
        index = self._request_index.get()
        self._arrivals += 1
        if self._arrivals == 1:
            self._leader_index = index
            await self._second_arrived.wait()
            return
        self._second_arrived.set()
        await self._finished[self._leader_index].wait()


async def race_same_purge_request(
    fixture: RegisteredLifecycleFixture,
    monkeypatch: pytest.MonkeyPatch,
    phase: NativePurgeRacePhase,
) -> tuple[Response, Response]:
    """Hold the second request at one phase until the first response commits."""
    gate = PurgeRaceGate()

    if phase == "prepare":
        original_lock = agent_purge_preparation.lock_lifecycle_target

        async def interleaved_lock(db: AsyncSession, agent_id: UUID) -> None:
            await gate.wait()
            await original_lock(db, agent_id)

        monkeypatch.setattr(agent_purge_preparation, "lock_lifecycle_target", interleaved_lock)
    elif phase == "snapshot":
        original_snapshot = agent_purge_service.materialize_conversation_history

        async def interleaved_snapshot(
            request: ConversationHistoryMaterializationRequest,
        ) -> ConversationHistoryManifest:
            await gate.wait()
            return await original_snapshot(request)

        monkeypatch.setattr(
            agent_purge_service,
            "materialize_conversation_history",
            interleaved_snapshot,
        )
    else:
        original_finalize = agent_purge_service.finalize_agent_purge

        async def interleaved_finalize(
            command: AgentPurgeCommand,
            prepared: PreparedAgentPurge,
        ) -> None:
            await gate.wait()
            await original_finalize(command, prepared)

        monkeypatch.setattr(agent_purge_service, "finalize_agent_purge", interleaved_finalize)

    responses: list[Response | None] = [None, None]
    async with AsyncExitStack() as stack:
        pairs = (
            await stack.enter_async_context(
                actor_client(fixture, fixture.actors.direct_org_admin_id)
            ),
            await stack.enter_async_context(
                actor_client(fixture, fixture.actors.direct_org_admin_id)
            ),
        )

        async def submit(index: int) -> None:
            gate.bind(index)
            client, csrf = pairs[index]
            try:
                responses[index] = await client.post(
                    f"/api/agents/{fixture.graph.root_agent_id}/purge",
                    json={"confirmation_name": "Lifecycle root"},
                    headers={"X-CSRF-Token": csrf},
                )
            finally:
                gate.finish(index)

        with fail_after(30):
            async with create_task_group() as tasks:
                tasks.start_soon(submit, 0)
                tasks.start_soon(submit, 1)

    first, second = responses
    assert first is not None and second is not None
    return first, second


__all__ = ["NativePurgeRacePhase", "PurgeRaceGate", "race_same_purge_request"]
