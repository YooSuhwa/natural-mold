"""Reusable native PostgreSQL graph for B08 lifecycle acceptance."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

type SentinelStage = Literal[
    "prepared",
    "registered",
    "started",
    "cancel-signaled",
    "terminal-ACK",
]


@dataclass(frozen=True, slots=True)
class LifecycleActors:
    owner_one_id: UUID
    owner_two_id: UUID
    direct_org_admin_id: UUID
    group_org_admin_id: UUID
    company_admin_id: UUID
    operator_id: UUID
    revoked_admin_id: UUID
    inactive_admin_id: UUID
    tenant_id: UUID
    org_id: UUID


@dataclass(frozen=True, slots=True)
class LifecycleGraph:
    tenant_id: UUID
    org_id: UUID
    root_owner_id: UUID
    child_owner_id: UUID
    root_agent_id: UUID
    child_agent_ids: tuple[UUID, ...]
    conversation_ids: tuple[UUID, ...]
    run_ids: tuple[UUID, ...]
    input_ids: tuple[UUID, ...]
    attachment_id: UUID
    artifact_id: UUID
    artifact_version_ids: tuple[UUID, UUID]
    api_run_id: UUID
    trigger_run_id: UUID
    share_link_id: UUID
    mcp_binding_id: UUID
    checkpoint_ids: tuple[str, ...]
    attachment_path: Path
    artifact_version_paths: tuple[Path, Path]


@dataclass(frozen=True, slots=True)
class LifecycleGraphProof:
    row_counts: tuple[tuple[str, int], ...]
    row_ids: tuple[tuple[str, tuple[str, ...]], ...]
    attachment_bytes: int
    artifact_version_bytes: tuple[int, int]
    attachment_sha256: str
    artifact_version_sha256: tuple[str, str]


@dataclass(slots=True)
class LifecycleSentinel:
    """Record explicit execution observations; mutation is the fixture's purpose."""

    prepared: int = 0
    registered: int = 0
    started: int = 0
    cancel_signaled: int = 0
    terminal_ack: int = 0
    provider_invocations: int = 0

    def observe(self, stage: SentinelStage) -> None:
        match stage:
            case "prepared":
                self.prepared += 1
            case "registered":
                self.registered += 1
            case "started":
                self.started += 1
            case "cancel-signaled":
                self.cancel_signaled += 1
            case "terminal-ACK":
                self.terminal_ack += 1

    @property
    def observed_stage_counts(self) -> tuple[int, int, int, int, int]:
        return (
            self.prepared,
            self.registered,
            self.started,
            self.cancel_signaled,
            self.terminal_ack,
        )


@dataclass(frozen=True, slots=True)
class RegisteredLifecycleFixture:
    sessions: async_sessionmaker[AsyncSession]
    actors: LifecycleActors
    graph: LifecycleGraph
    sentinel: LifecycleSentinel


@asynccontextmanager
async def registered_lifecycle_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[RegisteredLifecycleFixture]:
    """Yield a real-auth fixture backed by the runner-owned migrated database."""
    from tests.authz.agent_lifecycle_graph_seed import seed_lifecycle_graph
    from tests.authz.agent_lifecycle_registration import registered_lifecycle_actors

    sentinel = LifecycleSentinel()
    async with registered_lifecycle_actors(monkeypatch, sentinel) as registration:
        graph = await seed_lifecycle_graph(registration.sessions, registration.actors, sentinel)
        yield RegisteredLifecycleFixture(
            sessions=registration.sessions,
            actors=registration.actors,
            graph=graph,
            sentinel=sentinel,
        )


__all__ = [
    "LifecycleActors",
    "LifecycleGraph",
    "LifecycleGraphProof",
    "LifecycleSentinel",
    "RegisteredLifecycleFixture",
    "SentinelStage",
    "registered_lifecycle_fixture",
]
