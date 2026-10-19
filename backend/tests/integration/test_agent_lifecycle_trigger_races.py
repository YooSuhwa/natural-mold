"""Native PostgreSQL barriers for scheduled and forced trigger execution."""

from __future__ import annotations

from uuid import UUID

import anyio
import pytest
from httpx import Response
from sqlalchemy import select

from app.agent_runtime import trigger_executor
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.services import agent_execution_runtime, trigger_service
from app.services.agent_execution_runtime import RuntimeOwnershipRequest
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture
from tests.authz.agent_lifecycle_trigger_runtime_fixture import (
    archive_client,
    prepare_remote_trigger_run,
    seed_trigger,
)

pytestmark = pytest.mark.integration


async def test_native_scheduled_and_forced_runs_share_durable_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        agent_id, trigger_id = await seed_trigger(fixture, label="scheduled-forced")
        monkeypatch.setattr(trigger_executor, "async_session", fixture.sessions)
        monkeypatch.setattr(agent_execution_runtime, "async_session", fixture.sessions)

        async def invoke(_request: trigger_executor._TriggerInvocation) -> dict[str, str]:
            fixture.sentinel.provider_invocations += 1
            return {"result": "native"}

        monkeypatch.setattr(trigger_executor, "_invoke_trigger", invoke)

        scheduled = await trigger_executor.execute_trigger(str(trigger_id))
        forced = await trigger_executor.execute_trigger(str(trigger_id), force=True)

        assert scheduled is not None and scheduled.status == "success"
        assert forced is not None and forced.status == "success"
        async with fixture.sessions() as db:
            envelopes = tuple(
                await db.scalars(
                    select(AgentTriggerRun)
                    .where(AgentTriggerRun.trigger_id == trigger_id)
                    .order_by(AgentTriggerRun.created_at)
                )
            )
            run_ids = tuple(row.conversation_run_id for row in envelopes)
            assert all(run_id is not None for run_id in run_ids)
            runs = tuple(
                await db.scalars(select(ConversationRun).where(ConversationRun.id.in_(run_ids)))
            )
            participants = tuple(
                await db.scalars(
                    select(ConversationRunAgent).where(ConversationRunAgent.run_id.in_(run_ids))
                )
            )
            assert [row.source for row in envelopes] == ["scheduled", "run_now"]
            assert {row.status for row in runs} == {"completed"}
            assert {row.agent_id_snapshot for row in participants} == {agent_id}
            assert {row.role for row in participants} == {"root"}
        assert fixture.sentinel.provider_invocations == 2


async def test_native_archive_first_and_config_first_never_invoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        archive_agent_id, archive_trigger_id = await seed_trigger(fixture, label="archive-first")
        config_agent_id, config_trigger_id = await seed_trigger(fixture, label="config-first")
        monkeypatch.setattr(trigger_executor, "async_session", fixture.sessions)
        monkeypatch.setattr(agent_execution_runtime, "async_session", fixture.sessions)
        removed: list[UUID] = []
        monkeypatch.setattr("app.scheduler.remove_trigger_job", removed.append)

        async def forbidden_invoke(_request: trigger_executor._TriggerInvocation) -> None:
            fixture.sentinel.provider_invocations += 1

        monkeypatch.setattr(trigger_executor, "_invoke_trigger", forbidden_invoke)
        client, email = await archive_client(fixture)
        async with client:
            login = await client.post(
                "/api/auth/login",
                json={"email": email, "password": "fixture password 42"},
            )
            assert login.status_code == 200, login.text
            csrf = login.json()["csrf_token"]
            archived = await client.delete(
                f"/api/agents/{archive_agent_id}", headers={"X-CSRF-Token": csrf}
            )
            assert archived.status_code == 204, archived.text
            assert archive_trigger_id in removed
            assert (
                await trigger_executor.execute_trigger(str(archive_trigger_id), force=True) is None
            )

            config_reached = anyio.Event()
            release_config = anyio.Event()
            accepted = anyio.Event()
            archive_done = anyio.Event()
            archive_finished = anyio.Event()
            original_build = trigger_executor._configure_trigger_subagents
            original_run = agent_execution_runtime.run_owned_execution
            execution_result: list[AgentTriggerRun | None] = []
            archive_result: list[Response] = []

            async def held_build(request: trigger_executor._TriggerSubagentBuild) -> None:
                config_reached.set()
                await release_config.wait()
                await original_build(request)

            async def execute_config() -> None:
                execution_result.append(
                    await trigger_executor.execute_trigger(str(config_trigger_id))
                )

            async def archive_config() -> None:
                archive_result.append(
                    await client.delete(
                        f"/api/agents/{config_agent_id}", headers={"X-CSRF-Token": csrf}
                    )
                )
                archive_finished.set()

            async def held_run(request: RuntimeOwnershipRequest, operation):
                accepted.set()
                await archive_done.wait()
                return await original_run(request, operation)

            monkeypatch.setattr(trigger_executor, "_configure_trigger_subagents", held_build)
            monkeypatch.setattr(trigger_service, "run_owned_execution", held_run)
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(execute_config)
                await config_reached.wait()
                tasks.start_soon(archive_config)
                await anyio.sleep(0.05)
                assert not archive_result
                release_config.set()
                await accepted.wait()
                await archive_finished.wait()
                assert archive_result[0].status_code == 204, archive_result[0].text
                archive_done.set()

            config_run = execution_result[0]

        assert config_run is not None and config_run.status == "canceled"
        assert config_trigger_id in removed
        assert fixture.sentinel.provider_invocations == 0


async def test_native_launch_first_cancels_local_owner_and_preserves_remote_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        local_agent_id, local_trigger_id = await seed_trigger(fixture, label="launch-first")
        remote_agent_id, remote_trigger_id = await seed_trigger(fixture, label="remote-owner")
        monkeypatch.setattr(trigger_executor, "async_session", fixture.sessions)
        monkeypatch.setattr(agent_execution_runtime, "async_session", fixture.sessions)
        removed: list[UUID] = []
        monkeypatch.setattr("app.scheduler.remove_trigger_job", removed.append)
        started = anyio.Event()

        async def held_invoke(_request: trigger_executor._TriggerInvocation) -> None:
            fixture.sentinel.provider_invocations += 1
            started.set()
            await anyio.sleep_forever()

        monkeypatch.setattr(trigger_executor, "_invoke_trigger", held_invoke)
        client, email = await archive_client(fixture)
        async with client:
            login = await client.post(
                "/api/auth/login",
                json={"email": email, "password": "fixture password 42"},
            )
            assert login.status_code == 200, login.text
            csrf = login.json()["csrf_token"]
            canceled = anyio.Event()

            async def execute_local() -> None:
                try:
                    await trigger_executor.execute_trigger(str(local_trigger_id))
                except anyio.get_cancelled_exc_class():
                    canceled.set()
                    raise

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(execute_local)
                await started.wait()
                archived = await client.delete(
                    f"/api/agents/{local_agent_id}", headers={"X-CSRF-Token": csrf}
                )
                assert archived.status_code == 204, archived.text
                await canceled.wait()

            remote = await prepare_remote_trigger_run(
                fixture,
                agent_id=remote_agent_id,
                trigger_id=remote_trigger_id,
            )

            remote_archived = await client.delete(
                f"/api/agents/{remote_agent_id}", headers={"X-CSRF-Token": csrf}
            )
            assert remote_archived.status_code == 204, remote_archived.text

            invoked = False

            async def remote_operation() -> None:
                nonlocal invoked
                invoked = True

            with pytest.raises(Exception, match="보관된 에이전트"):
                await agent_execution_runtime.run_owned_execution(
                    RuntimeOwnershipRequest(
                        remote.run_id,
                        remote.conversation_id,
                        remote_agent_id,
                        remote.context,
                    ),
                    remote_operation,
                )

        async with fixture.sessions() as db:
            local_envelope = await db.scalar(
                select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == local_trigger_id)
            )
            assert local_envelope is not None and local_envelope.conversation_run_id is not None
            local_run = await db.get(ConversationRun, local_envelope.conversation_run_id)
            remote_run = await db.get(ConversationRun, remote.run_id)
            assert local_run is not None and local_run.status == "canceled"
            assert local_run.cancellation_acknowledged_at is not None
            assert remote_run is not None and remote_run.status == "canceling"
            assert remote_run.worker_instance_id == "remote-native-owner"
            assert remote_run.cancellation_acknowledged_at is None
        assert invoked is False
        assert {local_trigger_id, remote_trigger_id}.issubset(set(removed))
        assert fixture.sentinel.provider_invocations == 1
