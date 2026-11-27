"""The prepared chat child control channel requires a live lease for its exact actor."""

from dataclasses import replace
from functools import partial
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_child_controls import (
    child_control_descriptor,
    prepared_child_controls,
)
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.agent_runtime.runtime_hydration_guard import post_marker_execution
from app.config import settings
from app.database import session_tenant
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.models.organization import OrganizationMember
from tests.authz import prepared_adapter_testkit as adapter
from tests.authz import prepared_materialization_testkit as materialization
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import seed_graph
from tests.authz.test_prepared_child_launch import CATALOG, CHILD
from tests.conftest import TestSession


@pytest.mark.asyncio
async def test_prepared_child_control_requires_matching_digest_and_each_new_permit(
    db: AsyncSession,
) -> None:
    graph = await seed_graph(db, conversations=1)
    context = graph.context
    conversation_id = graph.conversation_ids[0]
    binding = PreparedRunBinding(
        context.tenant_id,
        context.org_id,
        context.user_id,
        graph.agent_id,
        conversation_id,
        uuid4(),
        str(conversation_id),
    )
    actor = uuid4()
    descriptor = child_control_descriptor(CHILD, actor)
    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    try:
        token = registry.reserve(binding)
        lease = replace(kit.lease(binding, token), descriptor_ids=frozenset({descriptor}))
        await registry.stage(lease)
        registry.activate(binding.run_id, token=token, digest=lease.digest)
        controls = partial(
            prepared_child_controls, lease=lease, catalog=CATALOG, session_factory=TestSession
        )
        for mismatched in ({CHILD: uuid4()}, {"root/general-purpose": actor}):
            with pytest.raises(DispatchDeniedError, match="child_control_not_prepared"):
                controls(mismatched)
        control = controls({CHILD: actor})[CHILD]
        async with control.session() as scoped:
            assert session_tenant(scoped) == context.tenant_id
        member = await db.get(OrganizationMember, (context.org_id, context.user_id))
        assert member is not None
        member.status = "suspended"
        await db.commit()
        with pytest.raises(DispatchDeniedError, match="forbidden"):
            async with control.session():
                pytest.fail("A revoked member opened a child control transaction.")
        await registry.terminalize(binding.run_id)
        with pytest.raises(DispatchDeniedError, match="prepared_run_lease_missing"):
            async with control.session():
                pytest.fail("A terminalized lease opened a child control transaction.")
    finally:
        reset_prepared_run_registry_for_tests()


@pytest.mark.asyncio
async def test_real_prepared_chat_adapter_freezes_and_tracks_only_linked_child(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = materialization.fresh_registry(monkeypatch)
    adapter.patch_keys(monkeypatch)
    monkeypatch.setattr(settings, "child_tasks_enabled", True)
    seeded = await adapter.seed(db)
    context = seeded.graph.context
    run = await db.get(ConversationRun, seeded.run_id)
    assert run is not None
    run.tenant_id, run.org_id = context.tenant_id, context.org_id
    run.run_source, run.credential_subject_user_id = "CHAT", context.user_id
    db.add(
        ConversationRunAgent(
            run_id=run.id,
            agent_id=seeded.child_agent_id,
            agent_id_snapshot=seeded.child_agent_id,
            role="subagent",
            path=[str(seeded.graph.agent_id), str(seeded.child_agent_id)],
        )
    )
    await db.commit()
    prepare = adapter.resolver(seeded, tmp_path)
    scripts = {
        seeded.root_model: [materialization.task_call(seeded.child_name, "t1"), AIMessage("done")],
        seeded.child_model: [AIMessage("Research done.")],
    }
    lease, _factory = await adapter.materialize(seeded, prepare, scripts)
    try:
        controls = {i for i in lease.descriptor_ids if i.startswith("child_control:")}
        assert controls == {
            child_control_descriptor(f"root/{seeded.child_name}", seeded.child_agent_id)
        }
        with post_marker_execution(str(seeded.run_id)):
            await lease.runnable.ainvoke({"messages": list(lease.messages)}, dict(lease.config))
        async with TestSession() as verify:
            child = (await verify.scalars(select(ConversationChildTask))).one()
        assert child.tool_call_id == "t1" and child.child_agent_id == seeded.child_agent_id
        assert child.status == "completed" and child.result == "Research done."
        assert child.checkpoint_ns and child.checkpoint_id
        assert [seeded.root_model, seeded.child_model, seeded.root_model] == materialization.SENDS
    finally:
        await registry.terminalize(seeded.run_id)
