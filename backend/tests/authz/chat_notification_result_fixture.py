"""A completed file goal whose exact artifact version is persisted by the recorder."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_goal import ConversationGoal, GoalTransitionIntent
from app.models.conversation_run import ConversationRun
from app.models.notification import Notification
from app.services.artifacts.recorder import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.chat_notification_outbox import (
    drain_chat_notification_outbox,
    enqueue_goal_transition,
)
from app.services.conversation_goal_evidence import inspect_goal_evidence
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession

RESULT_NAME = "comparison.csv"
ORIGINAL = "업체,가격\n가,100\n".encode()
UPDATED = "업체,가격\n가,12500\n".encode()


@dataclass
class GoalResult:
    conversation: Conversation
    run: ConversationRun
    goal: ConversationGoal
    intent: GoalTransitionIntent
    recorder: ArtifactDeltaRecorder
    artifact: ConversationArtifact
    first_version: ArtifactVersion
    source: Path
    stored_file: Path

    async def enqueue(self, db: AsyncSession) -> None:
        await enqueue_goal_transition(
            db,
            goal_intent_id=self.intent.id,
            goal_id=self.goal.id,
            version=self.goal.version,
            revision=self.goal.revision,
            kind="completed",
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            user_id=TEST_USER_ID,
            conversation_id=self.conversation.id,
            run_id=self.run.id,
            artifact_id=self.artifact.id,
            version_id=self.first_version.id,
        )

    async def deliver(self, db: AsyncSession) -> Notification:
        await self.enqueue(db)
        await db.commit()
        assert await drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 1
        await db.commit()
        return (await db.scalars(select(Notification))).one()


@pytest.fixture
async def goal_result(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> GoalResult:
    storage = tmp_path / "objects"
    monkeypatch.setattr(settings, "artifact_storage_dir", str(storage))
    monkeypatch.setattr(settings, "chat_work_notifications_enabled", True)
    conversation_id, agent_id = await seed_artifact_conversation()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = ConversationRun(
        conversation_id=conversation_id,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        agent_id=agent_id,
        status="completed",
        source="chat",
    )
    criteria = [{"kind": "nonempty_file", "logical_path": RESULT_NAME}]
    goal = ConversationGoal(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        status="completed",
        title="가격 비교표",
        criteria=criteria,
        version=2,
        revision=4,
    )
    db.add_all([run, goal])
    await db.commit()
    output = tmp_path / "results"
    output.mkdir()
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conversation_id,
            org_context=unit_context(TEST_USER_ID),
            agent_id=agent_id,
            assistant_msg_id=str(run.id),
            output_dir=output,
        ),
    )
    await recorder.prepare()
    source = output / RESULT_NAME
    source.write_bytes(ORIGINAL)
    assert await recorder.collect_after_tool_result(tool_name="write_file", tool_call_id="first")
    artifact = (await db.scalars(select(ConversationArtifact))).one()
    first = (await db.scalars(select(ArtifactVersion))).one()
    evidence = await inspect_goal_evidence(
        db, conversation=conversation, run=run, criteria=criteria
    )
    assert evidence.all_passed and evidence.checks[0].version_id == first.id
    goal.last_evidence = evidence.model_dump(mode="json")
    intent = GoalTransitionIntent(
        goal_id=goal.id,
        goal_version=goal.version,
        goal_revision=goal.revision,
        run_id=run.id,
        kind="completed",
        payload={"evidence": goal.last_evidence},
    )
    db.add(intent)
    await db.commit()
    return GoalResult(
        conversation,
        run,
        goal,
        intent,
        recorder,
        artifact,
        first,
        source,
        storage / first.object_key,
    )
