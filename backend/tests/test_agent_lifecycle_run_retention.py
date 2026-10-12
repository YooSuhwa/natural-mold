from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_api import AgentApiRun
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.token_usage import TokenUsage
from app.services import agent_execution_owner, conversation_run_service
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.test_agent_lifecycle_run_ownership import _seed_execution_tree


@pytest.mark.asyncio
async def test_legacy_unlinked_active_envelopes_are_stopping_conflicts(
    db: AsyncSession,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    api = AgentApiRun(
        public_id=f"legacy-{uuid.uuid4()}",
        user_id=root.user_id,
        deployment_id=None,
        deployment_public_id_snapshot="legacy",
        conversation_id=conversation.id,
        mode="background",
        status="running",
    )
    trigger = AgentTriggerRun(
        trigger_id=None,
        agent_id=root.id,
        trigger_name_snapshot="legacy",
        user_id=root.user_id,
        conversation_id=conversation.id,
        status="running",
        source="scheduled",
        input_message="legacy",
    )
    db.add_all([api, trigger])
    await db.flush()

    conflicts = await agent_execution_owner.active_unlinked_envelopes(db, (root.id,))
    assert conflicts.api_run_ids == (api.id,)
    assert conflicts.trigger_run_ids == (trigger.id,)


@pytest.mark.asyncio
async def test_participant_cancel_excludes_unrelated_child_standalone_run(
    db: AsyncSession,
) -> None:
    root, child, conversation = await _seed_execution_tree(db)
    parent_run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="parent",
    )
    db.add_all(
        [
            ConversationRunAgent(
                run_id=parent_run.id,
                agent_id=root.id,
                agent_id_snapshot=root.id,
                role="root",
                path=[],
            ),
            ConversationRunAgent(
                run_id=parent_run.id,
                agent_id=child.id,
                agent_id_snapshot=child.id,
                role="subagent",
                path=[str(root.id), str(child.id)],
            ),
        ]
    )
    child_conversation = Conversation(
        agent_id=child.id,
        user_id=child.user_id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot=child.name,
        title="Standalone child",
        last_activity_at=datetime.now(UTC),
    )
    db.add(child_conversation)
    await db.flush()
    child_run = await conversation_run_service.create_run(
        db,
        conversation_id=child_conversation.id,
        agent_id=child.id,
        context=unit_context(child.user_id),
        source="chat",
        input_preview="standalone",
    )
    db.add(
        ConversationRunAgent(
            run_id=child_run.id,
            agent_id=child.id,
            agent_id_snapshot=child.id,
            role="root",
            path=[],
        )
    )
    await db.flush()

    intents = await agent_execution_owner.stage_participant_cancel_intents(
        db,
        agent_execution_owner.ParticipantCancelRequest(root.id, "agent_archived"),
    )

    assert [intent.run_id for intent in intents] == [parent_run.id]
    assert parent_run.status == "canceling"
    assert child_run.status == "queued"


@pytest.mark.asyncio
async def test_terminal_usage_preserves_null_agent_id_after_purge(db: AsyncSession) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=root.id,
        context=unit_context(root.user_id),
        source="chat",
        input_preview="retained",
    )
    run.agent_id = None
    await db.flush()

    from app.agent_runtime.run_metrics_types import RunMetricsSnapshot
    from app.services.conversation_run_metrics_service import persist_run_metrics_and_usage

    snapshot = RunMetricsSnapshot(
        terminal_state="completed",
        elapsed_ms=1.0,
        ttft_ms=None,
        generation_ms=None,
        tokens_per_second=None,
        prompt_tokens=4,
        completion_tokens=2,
        cache_creation_tokens=None,
        cache_read_tokens=None,
        estimated_cost=None,
        usage_complete=True,
        root_tool_calls=0,
        descendant_tool_calls=0,
        root_subagent_calls=0,
        descendant_subagent_calls=0,
        activity=(),
        activity_truncated=False,
    )
    await persist_run_metrics_and_usage(db, run=run, snapshot=snapshot, model_name="retained")
    usage_agent_id = await db.scalar(select(TokenUsage.agent_id).where(TokenUsage.run_id == run.id))
    assert usage_agent_id is None
