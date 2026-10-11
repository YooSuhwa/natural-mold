"""Actual m83 detached workers retain private outputs through concurrent execution/recovery."""

import json
from pathlib import Path
from uuid import UUID

import anyio
import pytest

from app.authz.org_context import OrgContext
from app.config import settings
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.model import Model
from app.models.organization import Organization
from app.models.user import User
from app.services import conversation_run_queue_service as queue
from app.services import conversation_run_queue_worker as queue_worker
from app.services import conversation_run_service as runs
from app.services import conversation_run_worker as worker
from app.services import conversation_stream_service as streams
from app.services import memory_service, trace_storage
from app.services.artifacts.library import list_conversation_artifacts
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from tests.authz.native_company_selection import select_second_company
from tests.authz.native_detached_runtime import (
    DetachedLane,
    NativeProviderBoundary,
    create_lane,
    scope_headers,
)
from tests.authz.test_registration_policy_integration import SignupApp, signup_app

pytestmark = pytest.mark.integration


async def _assert_private_outputs(
    stack: SignupApp, lane: DetachedLane, foreign: OrgContext
) -> None:
    async with stack.sessions() as db:
        run = await runs.get_run_for_user(
            db, conversation_id=lane.conversation_id, run_id=lane.run_id, context=lane.context
        )
        assert run is not None and run.status == "completed" and not run.is_active
        assert (
            await runs.get_run_for_user(
                db, conversation_id=lane.conversation_id, run_id=lane.run_id, context=foreign
            )
            is None
        )
        records = await memory_service.list_runtime_memory_records(
            db, context=lane.context, agent_id=lane.agent_id, allowed_scopes="both"
        )
        assert [record.content for record in records] == [str(lane.context.org_id)]
        assert all(record.source_conversation_id == lane.conversation_id for record in records)
        trace = await trace_storage.get_trace_by_msg_id(db, str(lane.run_id))
        assert trace is not None and trace.conversation_id == lane.conversation_id
        encoded = json.dumps(trace.events)
        assert str(lane.context.org_id) in encoded and str(foreign.org_id) not in encoded
        artifacts = await list_conversation_artifacts(
            db, context=lane.context, conversation_id=lane.conversation_id
        )
        assert len(artifacts) == 1
        artifact_id = artifacts[0].id
    content = await stack.client.get(
        f"/api/artifacts/{artifact_id}/content", headers=scope_headers(stack, lane.context)
    )
    assert content.status_code == 200 and content.json()["text"] == str(lane.context.org_id)
    denied = await stack.client.get(
        f"/api/artifacts/{artifact_id}/content", headers=scope_headers(stack, foreign)
    )
    assert denied.status_code == 404


async def test_concurrent_detached_workers_and_recovery_remain_in_original_companies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", False)
    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", True)
    monkeypatch.setattr(settings, "data_root", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "conversation_output_dir", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "storage"))
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-detached@example.com",
                "name": "Detached fixture",
                "password": "native-detached-password",
            },
        )
        assert registered.status_code == 201, registered.text
        async with stack.sessions() as db:
            user = await db.get(User, UUID(registered.json()["user"]["id"]))
            assert user is not None and user.last_active_org_id is not None
            org = await db.get(Organization, user.last_active_org_id)
            assert org is not None
            first = OrgContext(user_id=user.id, org_id=org.id, tenant_id=org.tenant_id)
            model = Model(provider="e2e_scripted", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        first_lane = await create_lane(stack, first, model_id)
        second = await select_second_company(
            stack, scope_headers(stack, first), "native-detached@example.com"
        )
        second_lane = await create_lane(stack, second, model_id)
        lanes = (first_lane, second_lane)
        registry = worker.RunTaskRegistry(worker_instance_id="native-concurrent")
        boundary = NativeProviderBoundary(stack.sessions)
        monkeypatch.setattr(worker, "async_session", stack.sessions)
        monkeypatch.setattr(streams, "async_session", stack.sessions)
        monkeypatch.setattr(queue_worker, "async_session", stack.sessions)
        monkeypatch.setattr(queue_worker, "get_run_task_registry", lambda: registry)
        monkeypatch.setattr(worker, "get_run_task_registry", lambda: registry)
        monkeypatch.setattr(queue_worker, "execute_agent_stream_langgraph", boundary.stream)

        async def launch(lane: DetachedLane) -> None:
            started = await queue_worker.launch_claimed_input(lane.input_id)
            assert started is not None and started.id == lane.run_id
            task = registry.get(lane.run_id)
            assert task is not None
            await task

        try:
            async with anyio.create_task_group() as tasks:
                for lane in lanes:
                    tasks.start_soon(launch, lane)
                with anyio.fail_after(30):
                    await boundary.all_started.wait()
                # The two provider boundaries are genuinely live together. A real
                # preference switch must not redirect either already-restored scope.
                switched = await stack.client.post(
                    "/api/me/active-org",
                    headers=scope_headers(stack, second),
                    json={"org_id": str(first.org_id)},
                )
                assert switched.status_code == 200, switched.text
                boundary.release.set()
            assert boundary.observed == {
                str(first_lane.conversation_id): first,
                str(second_lane.conversation_id): second,
            }
            await _assert_private_outputs(stack, first_lane, second)
            await _assert_private_outputs(stack, second_lane, first)

            recovery_ids: dict[UUID, UUID] = {}
            recovery_inputs: dict[UUID, UUID] = {}
            for lane in lanes:
                async with stack.sessions() as db:
                    await queue.enqueue_input(
                        db,
                        context=lane.context,
                        conversation_id=lane.conversation_id,
                        client_request_id=f"recover-{lane.context.org_id}",
                        source="chat",
                        input_payload={"content": "Recovery fixture"},
                        attachment_ids=[],
                        checkpoint_id=None,
                        priority=0,
                    )
                    await db.commit()
                    claimed = await queue.claim_next_input(
                        db, context=lane.context, conversation_id=lane.conversation_id
                    )
                    assert claimed is not None
                    recovery_ids[lane.conversation_id] = claimed.run.id
                    recovery_inputs[lane.conversation_id] = claimed.input.id
                    await runs.request_cancel_run(db, claimed.run, reason="stop")
                    await db.commit()
            recovered: list[UUID] = []

            async def recover() -> None:
                recovered.extend(
                    await finalize_workerless_cancel_before_start(
                        session_factory=stack.sessions,
                        protected_run_ids=(),
                    )
                )

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(recover)
                tasks.start_soon(recover)
            assert len(recovered) == 2 and set(recovered) == set(recovery_ids)
            async with stack.sessions() as db:
                for lane in lanes:
                    recovered_run = await db.get(
                        ConversationRun, recovery_ids[lane.conversation_id]
                    )
                    assert recovered_run is not None and recovered_run.status == "canceled"
                    assert recovered_run.conversation_id == lane.conversation_id
                    queued = await db.get(
                        ConversationRunInput, recovery_inputs[lane.conversation_id]
                    )
                    assert queued is not None and queued.status == "canceled"
                    assert queued.conversation_id == lane.conversation_id
                    assert queued.run_id is None
            await _assert_private_outputs(stack, first_lane, second)
            await _assert_private_outputs(stack, second_lane, first)
            assert len(boundary.observed) == 2  # recovery never invokes a provider
        finally:
            boundary.release.set()
            await registry.shutdown()
