"""Persistence contracts for immutable API and trigger envelopes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import runtime_service
from app.agent_api.dependencies import ApiKeyPrincipal
from app.models.agent_api import AgentApiRun, AgentApiThread
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.schemas.agent_api import AgentThreadCreateRequest
from app.services import trigger_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


async def test_api_envelopes_persist_original_deployment_public_id_snapshot(
    db: AsyncSession,
    api_case: ApiCase,
    private_scopes: PrivateScopes,
) -> None:
    # Given: an accepted deployment with an input-derived public identifier.
    deployment = api_case.first
    deployment_public_id = "snapshot-deployment-original"
    deployment.public_id = deployment_public_id
    await db.commit()
    principal = ApiKeyPrincipal(api_case.key, private_scopes.first.user_id, private_scopes.first)

    # When: actual API Thread and Run writers persist their envelopes without a provider invocation.
    thread = await runtime_service.create_thread(
        db,
        principal=principal,
        deployment=deployment,
        request=AgentThreadCreateRequest(agent_id=deployment_public_id),
    )
    run = await runtime_service.create_run_row(
        db,
        principal=principal,
        deployment=deployment,
        conversation_id=None,
        thread_id=thread.id,
        mode="wait",
        input_payload={},
        metadata=None,
    )
    deployment.public_id = "snapshot-deployment-renamed"
    await db.commit()
    db.expunge_all()

    # Then: stored envelopes retain the accepted identifier after a later rename.
    stored_thread = await db.scalar(select(AgentApiThread).where(AgentApiThread.id == thread.id))
    stored_run = await db.scalar(select(AgentApiRun).where(AgentApiRun.id == run.id))
    assert stored_thread is not None
    assert stored_run is not None
    assert stored_thread.deployment_public_id_snapshot == deployment_public_id
    assert stored_run.deployment_public_id_snapshot == deployment_public_id


async def test_trigger_run_persists_original_trigger_name_snapshot(
    db: AsyncSession,
    api_case: ApiCase,
    private_scopes: PrivateScopes,
) -> None:
    # Given: a persisted trigger whose name is distinct from its later rename.
    trigger_name = "Snapshot trigger original"
    trigger = AgentTrigger(
        org_id=private_scopes.first.org_id,
        tenant_id=private_scopes.first.tenant_id,
        agent_id=api_case.first.agent_id,
        user_id=private_scopes.first.user_id,
        name=trigger_name,
        trigger_type="interval",
        schedule_config={"interval_minutes": 10},
        input_message="Snapshot input",
    )
    db.add(trigger)
    await db.commit()

    # When: the real trigger-run writer creates its durable envelope.
    run = await trigger_service.start_trigger_run(db, trigger)
    trigger.name = "Snapshot trigger renamed"
    await db.commit()
    db.expunge_all()

    # Then: the stored run keeps the accepted trigger name.
    stored_run = await db.scalar(select(AgentTriggerRun).where(AgentTriggerRun.id == run.id))
    assert stored_run is not None
    assert stored_run.trigger_name_snapshot == trigger_name
