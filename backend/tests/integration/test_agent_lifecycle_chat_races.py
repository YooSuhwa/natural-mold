from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TypedDict, Unpack
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select

from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.observability.langfuse import LangfuseTraceRecord
from app.schemas.conversation_refs import JsonValue
from app.schemas.conversation_scope import ConversationPromotion
from app.services import (
    agent_execution_owner,
    conversation_run_queue_service,
    conversation_run_queue_worker,
    conversation_run_worker,
)
from app.services.agent_deletion import AgentArchiveRequest, stage_agent_archive
from app.services.agent_execution_owner import OwnedExecutionRequest, notify_local_owner
from app.services.artifact_service import ArtifactDeltaRecorder
from app.services.chat.conversations import (
    create_conversation,
    prepare_executable_conversation,
    promote_draft_conversation,
)
from app.services.conversation_run_terminal_delivery import OwnerTerminalDelivery
from tests.authz.agent_lifecycle_fixture import (
    registered_lifecycle_fixture,
)
from tests.authz.run_fixture_config import run_fixture_config
from tests.integration.agent_lifecycle_chat_races_support import (
    _seed_same_owner_chat,
    assert_native_revoked_inactive_recovery_preserves_history_without_payload_hydration,
)


class _ExecutorKeywordArgs(TypedDict):
    moldy_source: str
    trace_sink: list[dict[str, JsonValue]]
    msg_id_sink: list[str]
    error_sink: list[StreamErrorRecord]
    broker: OwnerTerminalDelivery
    persist_callback: Callable[[list[dict[str, JsonValue]]], Awaitable[None]]
    run_id: str
    langfuse_sink: list[LangfuseTraceRecord]
    run_metrics: RunMetricsAccumulator
    artifact_recorder: ArtifactDeltaRecorder | None


async def test_native_archive_first_config_first_and_recovery_zero_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        context, root_id, conversation_id, draft_id = await _seed_same_owner_chat(fixture)
        monkeypatch.setattr(conversation_run_queue_worker, "async_session", fixture.sessions)
        async with fixture.sessions() as db:
            queued = await conversation_run_queue_service.enqueue_input(
                db,
                conversation_id=conversation_id,
                context=context,
                client_request_id="task9-claimed-before-archive",
                source="chat",
                input_payload={"messages": [{"role": "user", "content": "queued"}]},
                attachment_ids=[],
                checkpoint_id=None,
                priority=0,
            )
            claimed = await conversation_run_queue_service.claim_next_input(
                db, conversation_id=conversation_id, context=context
            )
            assert claimed is not None and claimed.input.id == queued.id
            claimed_revision = claimed.input.revision
            # An execution proof is bound to one transaction's lock scope: commit the claim
            # before proving the second (draft) conversation (5d927df9).
            await db.commit()
            await prepare_executable_conversation(db, draft_id, context)
            await db.commit()
            participants = await db.scalar(
                select(func.count())
                .select_from(ConversationRunAgent)
                .where(ConversationRunAgent.run_id == claimed.run.id)
            )
            assert participants == 2
            mutation = await stage_agent_archive(
                db,
                AgentArchiveRequest(root_id, context.user_id, utc_now_naive()),
            )
            await db.commit()

        for intent in mutation.cancel_intents:
            assert not notify_local_owner(intent, conversation_run_worker.get_run_task_registry())
        async with fixture.sessions() as db:
            with pytest.raises(AppError) as accept_error:
                await agent_execution_owner.accept_owned_execution(
                    db,
                    OwnedExecutionRequest(
                        draft_id, root_id, context, "chat", "blocked", run_source="chat"
                    ),
                )
            with pytest.raises(AppError):
                await create_conversation(db, root_id, "blocked", context=context)
            with pytest.raises(AppError):
                await conversation_run_queue_service.enqueue_input(
                    db,
                    conversation_id=draft_id,
                    context=context,
                    client_request_id="blocked-after-archive",
                    source="chat",
                    input_payload={"messages": []},
                    attachment_ids=[],
                    checkpoint_id=None,
                    priority=0,
                )
            with pytest.raises(AppError):
                await conversation_run_queue_service.resume_queue(
                    db, conversation_id=draft_id, context=context
                )
            with pytest.raises(AppError):
                await promote_draft_conversation(
                    db, ConversationPromotion(draft_id, context, "blocked")
                )
            assert accept_error.value.code == "AGENT_ARCHIVED"
            await db.rollback()

        launch_attempts: list[UUID] = []

        async def observe_recovery_scan(input_id: UUID) -> ConversationRun | None:
            launch_attempts.append(input_id)
            return None

        monkeypatch.setattr(
            conversation_run_queue_worker,
            "launch_claimed_input",
            observe_recovery_scan,
        )
        assert await conversation_run_queue_worker.recover_conversation_queue() == 0
        assert queued.id not in launch_attempts
        async with fixture.sessions() as db:
            run = await db.get(ConversationRun, claimed.run.id)
            retained = await db.get(ConversationRunInput, queued.id)
            draft = await db.get(Conversation, draft_id)
            assert run is not None and run.status == "canceled"
            assert run.cancellation_acknowledged_at is not None
            assert retained is not None
            assert (retained.status, retained.run_id) == ("canceled", None)
            assert retained.revision == claimed_revision + 1
            assert draft is not None and draft.source == "draft"
        assert fixture.sentinel.provider_invocations == 0


async def test_native_launch_first_owner_cancels_and_acknowledges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        context, root_id, conversation_id, _draft_id = await _seed_same_owner_chat(fixture)
        monkeypatch.setattr(conversation_run_worker, "async_session", fixture.sessions)
        async with fixture.sessions() as db:
            accepted = await agent_execution_owner.accept_owned_execution(
                db,
                OwnedExecutionRequest(conversation_id, root_id, context, "chat", "launch"),
            )
            await db.commit()
        cfg = await run_fixture_config(accepted.run_id, fixture.sessions)
        registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="task9-owner")
        started = anyio.Event()

        async def blocking_executor(
            _cfg: AgentConfig,
            _payload: dict[str, JsonValue],
            **_kwargs: Unpack[_ExecutorKeywordArgs],
        ) -> AsyncGenerator[str, None]:
            fixture.sentinel.provider_invocations += 1
            started.set()
            await anyio.sleep_forever()
            if False:
                yield ""

        await conversation_run_worker.start_conversation_run(
            run_id=accepted.run_id,
            conversation_id=conversation_id,
            cfg=cfg,
            user=CurrentUser(id=context.user_id, email="task9@example.com", name="Task9"),
            input_payload={"messages": []},
            moldy_source="chat",
            executor_fn=blocking_executor,
            registry=registry,
        )
        task = registry.get(accepted.run_id)
        assert task is not None
        with anyio.fail_after(5):
            await started.wait()
        async with fixture.sessions() as db:
            mutation = await stage_agent_archive(
                db,
                AgentArchiveRequest(root_id, context.user_id, utc_now_naive()),
            )
            await db.commit()
        assert any(notify_local_owner(intent, registry) for intent in mutation.cancel_intents)
        with contextlib.suppress(anyio.get_cancelled_exc_class()):
            await task
        async with fixture.sessions() as db:
            run = await db.get(ConversationRun, accepted.run_id)
            assert run is not None and run.status == "canceled" and not run.is_active
            assert run.worker_instance_id == registry.worker_instance_id
            assert run.cancellation_acknowledged_at is not None
        assert fixture.sentinel.provider_invocations == 1


async def test_native_revoked_inactive_recovery_preserves_history_without_payload_hydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await assert_native_revoked_inactive_recovery_preserves_history_without_payload_hydration(
        monkeypatch
    )
