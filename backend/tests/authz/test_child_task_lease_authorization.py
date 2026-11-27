"""An archived organization's existing child work is bound to its live prepared permit."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_child_controls import (
    child_control_descriptor,
    prepared_child_controls,
)
from app.agent_runtime.prepared_run_digest import compute_prepared_run_digest
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.prepared_subagent_catalog import (
    ACTOR_ID_KEY,
    PreparedChildCatalog,
    PreparedChildPolicy,
    build_prepared_child_catalog,
)
from app.agent_runtime.runtime_config import PreparedRunBinding, PreparedRunLease
from app.agent_runtime.runtime_hydration_guard import post_marker_execution
from app.authz.org_context import OrgContext
from app.authz.organization_execution_proof import live_proof
from app.config import settings
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization, OrganizationMember
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.child_task_authorization import authorize_child_target
from app.services.child_task_errors import ChildTaskStopped
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import seed_graph
from tests.conftest import TestSession

if TYPE_CHECKING:
    from app.agent_runtime.delegated_child_control import ChildTaskLifecycle


@dataclass(frozen=True)
class ArchivedRun:
    context: OrgContext
    lease: PreparedRunLease
    catalog: PreparedChildCatalog
    actors: dict[str, UUID]

    def control(self, lease: PreparedRunLease) -> ChildTaskLifecycle:
        return prepared_child_controls(
            self.actors, lease=lease, catalog=self.catalog, session_factory=TestSession
        )["root/researcher"]


@pytest.fixture
async def archived_run(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ArchivedRun]:
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    graph = await seed_graph(db, conversations=1)
    context = graph.context
    parent = await db.get(Agent, graph.agent_id)
    assert parent is not None
    agents = [
        Agent(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            model_id=parent.model_id,
            name=name,
            system_prompt="Test agent",
        )
        for name in ["researcher", "writer"]
    ]
    db.add_all(agents)
    await db.flush()
    actors = {f"root/{child.name}": child.id for child in agents}
    db.add_all(
        [
            AgentSubAgentLink(
                parent_agent_id=parent.id, sub_agent_id=child.id, delegated_by=context.user_id
            )
            for child in agents
        ]
    )
    await db.commit()
    conversation_id = graph.conversation_ids[0]
    accepted = await accept_owned_execution(
        db, OwnedExecutionRequest(conversation_id, parent.id, context, "chat", None)
    )
    catalog = build_prepared_child_catalog(
        [
            {
                "name": child.name,
                "description": child.name,
                "system_prompt": "Test agent",
                ACTOR_ID_KEY: child.id,
            }
            for child in agents
        ],
        model="local",
        tools=[],
        permissions=None,
        stored_policy={path: PreparedChildPolicy("local", frozenset()) for path in actors},
    )
    binding = PreparedRunBinding(
        context.tenant_id,
        context.org_id,
        context.user_id,
        parent.id,
        conversation_id,
        accepted.run_id,
        str(conversation_id),
        participant_paths=tuple(catalog.children),
    )
    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    try:
        descriptors = frozenset(
            child_control_descriptor(path, actor) for path, actor in actors.items()
        )
        material = kit.material(
            binding, descriptor_ids=descriptors, snapshot_digests={"children": catalog.digest}
        )
        lease = replace(
            kit.lease(binding, registry.reserve(binding)),
            descriptor_ids=descriptors,
            digest=compute_prepared_run_digest(material),
        )
        await registry.stage(lease)
        registry.activate(accepted.run_id, token=lease.token, digest=lease.digest)
        run = await db.get(ConversationRun, accepted.run_id)
        org = await db.get(Organization, context.org_id)
        assert run is not None and org is not None
        run.status, run.is_active = "running", True
        run.runtime_materialized_at, run.runtime_materialization_sha256 = (
            datetime.now(UTC),
            lease.digest,
        )
        org.status, org.archived_at = "archived", datetime.now(UTC)
        await db.commit()
        yield ArchivedRun(context, lease, catalog, actors)
    finally:
        reset_prepared_run_registry_for_tests()


async def test_archived_org_admits_exact_child_only_with_its_prepared_control(
    archived_run: ArchivedRun,
) -> None:
    case = archived_run
    run_id = case.lease.binding.run_id
    with post_marker_execution(str(run_id)):
        async with case.control(case.lease).session() as session:
            run = await authorize_child_target(
                session, case.context, run_id, case.actors["root/researcher"]
            )
            proof = live_proof(session)
            assert run.id == run_id and proof is not None and proof.archived_bypass
        async with case.control(case.lease).session() as session:
            with pytest.raises(ChildTaskStopped) as err:
                await authorize_child_target(
                    session, case.context, run_id, case.actors["root/writer"]
                )
            assert err.value.reason == "revoked"


async def test_archived_org_denies_http_even_with_valid_lease_in_worker(
    db: AsyncSession, archived_run: ArchivedRun
) -> None:
    case = archived_run
    with pytest.raises(ChildTaskStopped) as err:
        await authorize_child_target(
            db, case.context, case.lease.binding.run_id, case.actors["root/researcher"]
        )
    assert err.value.reason == "revoked"


async def test_control_refuses_forged_token_before_leaf(archived_run: ArchivedRun) -> None:
    case = archived_run
    with pytest.raises(DispatchDeniedError):
        async with case.control(replace(case.lease, token="invalid")).session():
            pytest.fail("A forged token reached the authorized child operation")


async def test_materialization_changed_since_lease_denies_authorized_child(
    db: AsyncSession, archived_run: ArchivedRun
) -> None:
    case = archived_run
    run_id = case.lease.binding.run_id
    run = await db.get(ConversationRun, run_id)
    assert run is not None
    run.runtime_materialization_sha256 = "0" * 64
    await db.commit()
    with post_marker_execution(str(run_id)):
        async with case.control(case.lease).session() as session:
            with pytest.raises(ChildTaskStopped) as err:
                await authorize_child_target(
                    session, case.context, run_id, case.actors["root/researcher"]
                )
            assert err.value.reason == "revoked"


async def test_organization_member_revocation_also_refuses_an_exact_lease(
    db: AsyncSession, archived_run: ArchivedRun
) -> None:
    case = archived_run
    member = await db.get(OrganizationMember, (case.context.org_id, case.context.user_id))
    assert member is not None
    member.status = "suspended"
    await db.commit()
    with pytest.raises(DispatchDeniedError):
        async with case.control(case.lease).session():
            pytest.fail("A suspended member reached the authorized child operation")


async def test_active_org_worker_needs_its_leaf_permit_while_http_checks_live_scope(
    db: AsyncSession, archived_run: ArchivedRun
) -> None:
    case = archived_run
    org = await db.get(Organization, case.context.org_id)
    assert org is not None
    org.status, org.archived_at = "active", None
    await db.commit()
    run_id, actor_id = case.lease.binding.run_id, case.actors["root/researcher"]
    with post_marker_execution(str(run_id)), pytest.raises(ChildTaskStopped) as denied:
        await authorize_child_target(db, case.context, run_id, actor_id)
    assert denied.value.reason == "revoked"
    await db.rollback()
    assert (await authorize_child_target(db, case.context, run_id, actor_id)).id == run_id
