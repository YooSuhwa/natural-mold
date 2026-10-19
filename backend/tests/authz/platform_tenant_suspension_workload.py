"""Representative private workload seeded before an operator suspends the company."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import service as agent_api_service
from app.agent_runtime.lease_dispatch_permit import DispatchPermit, open_dispatch_permit
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.runtime_config import PreparedRunBinding, PreparedRunLease
from app.authz.org_context import OrgContext
from app.models.agent_api import AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.organization import Organization
from app.schemas.agent import AgentCreate
from app.schemas.agent_api import AgentApiKeyCreate, AgentDeploymentCreate
from app.services import agent_service
from app.services.chat.chat_execution_preparation import create_conversation
from tests.authz import prepared_run_testkit

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from tests.authz.platform_tenant_lifecycle_app import LifecycleCase


@dataclass(frozen=True, slots=True)
class SuspensionWorkload:
    agent_id: UUID
    conversation_id: UUID
    trigger_id: UUID
    paused_trigger_id: UUID
    running_run_id: UUID
    deployment_public_id: str
    disabled_deployment_id: UUID
    api_key: str
    api_key_id: UUID
    archived_org_id: UUID


def _trigger(case: LifecycleCase, agent_id: UUID, status: str) -> AgentTrigger:
    return AgentTrigger(
        agent_id=agent_id,
        user_id=case.member_id,
        org_id=case.org_id,
        tenant_id=case.tenant_id,
        name=f"Task 13 {status}",
        trigger_type="interval",
        schedule_config={"minutes": 30},
        input_message="scheduled hello",
        timezone="Asia/Seoul",
        conversation_policy="new_per_run",
        status=status,
    )


async def seed_suspension_workload(db: AsyncSession, case: LifecycleCase) -> SuspensionWorkload:
    context = case.context
    model = Model(provider="openai", model_name="task13-fixture", display_name="Fixture")
    db.add(model)
    await db.commit()
    agent, second = [
        await agent_service.create_agent(
            db,
            AgentCreate(
                name=name, system_prompt="Fixture", model_id=model.id, identity_mode="fixed"
            ),
            context,
        )
        for name in ("Task 13 agent", "Task 13 second agent")
    ]
    conversation = await create_conversation(db, agent.id, "Task 13", context=context)
    deployment = await agent_api_service.create_deployment(
        db, context, AgentDeploymentCreate(agent_id=agent.id)
    )
    disabled = await agent_api_service.create_deployment(
        db, context, AgentDeploymentCreate(agent_id=second.id)
    )
    assert disabled.id != deployment.id
    key, cleartext = await agent_api_service.create_api_key(
        db,
        context,
        AgentApiKeyCreate(name="Task 13", scopes=["read", "invoke"], allow_all_deployments=True),
    )
    active, paused = _trigger(case, agent.id, "active"), _trigger(case, agent.id, "paused")
    running = ConversationRun(
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=case.member_id,
        source="chat",
        status="running",
        is_active=True,
    )
    archived = Organization(
        tenant_id=case.tenant_id,
        name="Archived sibling",
        slug=f"archived-{uuid4().hex[:8]}",
        status="archived",
        archived_at=datetime.now(UTC),
    )
    db.add_all((active, paused, running, archived))
    stored_disabled = await db.get(AgentDeployment, disabled.id)
    assert stored_disabled is not None
    stored_disabled.status = "disabled"
    await db.commit()
    return SuspensionWorkload(
        agent_id=agent.id,
        conversation_id=conversation.id,
        trigger_id=active.id,
        paused_trigger_id=paused.id,
        running_run_id=running.id,
        deployment_public_id=deployment.public_id,
        disabled_deployment_id=disabled.id,
        api_key=cleartext,
        api_key_id=key.id,
        archived_org_id=archived.id,
    )


DESCRIPTOR = "provider:root"


async def activate_lease(
    context: OrgContext, agent_id: UUID, conversation_id: UUID, run_id: UUID
) -> PreparedRunLease:
    """Stage and activate a real prepared lease in a fresh process-local registry."""
    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    binding = PreparedRunBinding(
        context.tenant_id, context.org_id, context.user_id, agent_id, conversation_id, run_id, "t"
    )
    token = registry.reserve(binding)
    lease = prepared_run_testkit.lease(binding, token)
    await registry.stage(lease)
    registry.activate(binding.run_id, token=token, digest=lease.digest)
    return lease


def provider_permit(
    lease: PreparedRunLease, sessions: Callable[[], AsyncSession]
) -> AbstractAsyncContextManager[DispatchPermit]:
    return open_dispatch_permit(
        lease,
        kind="provider",
        descriptor_id=DESCRIPTOR,
        allowed_descriptors=frozenset({DESCRIPTOR}),
        session_factory=sessions,
    )
