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
    lease_for_child_control,
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
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.models.organization import OrganizationMember
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
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
            assert (
                lease_for_child_control(
                    scoped, context=context, run_id=binding.run_id, child_agent_id=actor
                )
                is lease
            )
            with pytest.raises(DispatchDeniedError, match="child_control_scope_mismatch"):
                lease_for_child_control(
                    scoped,
                    context=replace(context, is_super_user=True),
                    run_id=binding.run_id,
                    child_agent_id=actor,
                )
            for other_run, other_actor in ((uuid4(), actor), (binding.run_id, uuid4())):
                with pytest.raises(DispatchDeniedError, match="child_control_scope_mismatch"):
                    lease_for_child_control(
                        scoped, context=context, run_id=other_run, child_agent_id=other_actor
                    )
        assert (
            lease_for_child_control(
                scoped, context=context, run_id=binding.run_id, child_agent_id=actor
            )
            is None
        )
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
    # The canned adapter seed predates immutable participant identities.
    # Enter through the actual chat admission to capture the owner and child pins.
    await db.delete(run)
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            seeded.request.conversation_id, seeded.graph.agent_id, context, "chat", None
        ),
    )
    seeded = replace(
        seeded, run_id=accepted.run_id, request=replace(seeded.request, run_id=accepted.run_id)
    )
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "prior_status", "carry"),
    [
        ("resume", "awaiting_approval", True),
        ("chat", "awaiting_approval", False),
        ("resume", "completed", False),
    ],
)
async def test_disabled_rollout_rebinds_controls_only_for_unfinished_resume(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    prior_status: str,
    carry: bool,
) -> None:
    registry = materialization.fresh_registry(monkeypatch)
    adapter.patch_keys(monkeypatch)
    monkeypatch.setattr(settings, "child_tasks_enabled", False)
    seeded = await adapter.seed(db)
    ctx = seeded.graph.context
    prior = await db.get(ConversationRun, seeded.run_id)
    assert prior is not None
    prior.status, prior.is_active = "interrupted", False
    prior.org_id, prior.tenant_id, prior.run_source = ctx.org_id, ctx.tenant_id, "CHAT"
    await db.commit()
    db.add(
        ConversationChildTask(
            run_id=prior.id,
            current_run_id=prior.id,
            tool_call_id="pending-task",
            checkpoint_ns="tools:parent",
            child_agent_id=seeded.child_agent_id,
            role=seeded.child_name,
            status=prior_status,
        )
    )
    run = ConversationRun(
        conversation_id=prior.conversation_id,
        agent_id=prior.agent_id,
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        run_source="CHAT",
        source=source,
        parent_run_id=prior.id if source == "resume" else None,
        status="queued",
        is_active=True,
    )
    db.add(run)
    await db.commit()
    resumed = replace(seeded, run_id=run.id, request=replace(seeded.request, run_id=run.id))
    prepare = adapter.resolver(resumed, tmp_path)
    scripts = {seeded.root_model: [AIMessage("done")], seeded.child_model: [AIMessage("done")]}
    lease, _factory = await adapter.materialize(resumed, prepare, scripts)
    try:
        descriptors = {d for d in lease.descriptor_ids if d.startswith("child_control:")}
        expected = child_control_descriptor(f"root/{seeded.child_name}", seeded.child_agent_id)
        assert descriptors == ({expected} if carry else set())
    finally:
        await registry.terminalize(run.id)
