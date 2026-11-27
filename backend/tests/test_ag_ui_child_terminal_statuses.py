"""A sibling's outcome is attached only to its authorized, physical invocation."""

from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.authz.org_context import OrgContext
from app.child_tasks.persistence import begin_child_task, set_child_task_status
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_child_terminal_statuses import proven_child_terminal_statuses
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.child_task_read_bindings import readable_child_task_visibility
from app.services.conversation_run_state import transition_run
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case

ALPHA = ("tools:alpha", "task:research")
BETA = ("tools:beta", "task:research")


@dataclass(frozen=True)
class Siblings:
    run: ConversationRun
    conversation: Conversation
    context: OrgContext
    alpha: UUID
    beta: UUID
    visibility: ChildVisibility

    async def outcomes(
        self,
        db: AsyncSession,
        *,
        visibility: ChildVisibility | None = None,
        context: OrgContext | None = None,
    ) -> dict[tuple[str, ...], str]:
        return await proven_child_terminal_statuses(
            db,
            conversation=self.conversation,
            context=context or self.context,
            run=self.run,
            visibility=visibility or self.visibility,
        )


@pytest.fixture
async def siblings(
    db: AsyncSession, child_case: ChildCase, request: pytest.FixtureRequest
) -> Siblings:
    context = child_case.graph.context
    run = await db.get(ConversationRun, child_case.run_id)
    assert run is not None
    conversation = await db.get(Conversation, run.conversation_id)
    assert conversation is not None

    async def start_child(call: str, ns: tuple[str, ...], status: str) -> UUID:
        child_id = await begin_child_task(
            db,
            context=context,
            conversation_id=conversation.id,
            run_id=run.id,
            tool_call_id=call,
            child_agent_id=child_case.child_id,
            role="researcher",
            checkpoint_ns="|".join(ns),
            brief="Private instructions",
        )
        assert child_id is not None
        await db.commit()
        await set_child_task_status(
            db,
            context=context,
            child_id=child_id,
            run_id=run.id,
            status=status,
            result="Private result" if status == "completed" else None,
        )
        await db.commit()
        return child_id

    alpha = await start_child("call-alpha", ALPHA, "awaiting_approval")
    beta = await start_child("call-beta", BETA, getattr(request, "param", "completed"))
    await transition_run(db, run, "interrupted", interrupt_id="approval-alpha")
    await db.commit()
    visibility = await readable_child_task_visibility(db, conversation, context, run.id)
    assert visibility.bindings == {
        ("call-alpha", ALPHA, str(alpha)),
        ("call-beta", BETA, str(beta)),
    }
    return Siblings(run, conversation, context, alpha, beta, visibility)


@pytest.mark.parametrize(
    ("siblings", "expected"),
    [("completed", "completed"), ("failed", "failed"), ("cancelled", "cancelled")],
    indirect=["siblings"],
)
async def test_only_the_finished_sibling_gets_its_persisted_outcome(
    db: AsyncSession, siblings: Siblings, expected: str
) -> None:
    def assert_private_metadata_only(state: ORMExecuteState) -> None:
        assert state.is_select
        assert getattr(state.statement, "_for_update_arg", None) is None
        sql = str(state.statement)
        assert "conversation_child_tasks.result" not in sql
        assert "conversation_child_tasks.brief" not in sql
        assert "credentials.data_encrypted" not in sql

    event.listen(db.sync_session, "do_orm_execute", assert_private_metadata_only)
    try:
        assert await siblings.outcomes(db) == {BETA: expected}
    finally:
        event.remove(db.sync_session, "do_orm_execute", assert_private_metadata_only)


async def test_reader_requires_the_exact_three_part_binding(
    db: AsyncSession, siblings: Siblings
) -> None:
    crossed = replace(
        siblings.visibility,
        bindings=frozenset(
            {
                ("call-alpha", BETA, str(siblings.beta)),
                ("call-beta", BETA, str(siblings.alpha)),
                ("call-beta", (*BETA, "tool:descendant"), str(siblings.beta)),
            }
        ),
    )
    assert await siblings.outcomes(db, visibility=crossed) == {}
    for context in (
        replace(siblings.context, user_id=uuid4()),
        replace(siblings.context, org_id=uuid4()),
        replace(siblings.context, tenant_id=uuid4()),
    ):
        assert await siblings.outcomes(db, context=context) == {}


async def test_saved_visibility_cannot_survive_a_revoked_child_link(
    db: AsyncSession, siblings: Siblings, child_case: ChildCase
) -> None:
    assert await siblings.outcomes(db) == {BETA: "completed"}
    await db.execute(
        delete(AgentSubAgentLink).where(
            AgentSubAgentLink.parent_agent_id == child_case.graph.agent_id,
            AgentSubAgentLink.sub_agent_id == child_case.child_id,
        )
    )
    await db.commit()
    assert await siblings.outcomes(db) == {}


async def test_terminal_outcome_after_parent_cutoff_does_not_change_replay(
    db: AsyncSession, siblings: Siblings
) -> None:
    assert siblings.run.completed_at is not None
    after = siblings.run.completed_at + timedelta(seconds=1)
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.id == siblings.alpha)
        .values(status="cancelled", updated_at=after)
    )
    await db.commit()
    assert await siblings.outcomes(db) == {BETA: "completed"}


async def test_same_binding_from_a_later_resume_does_not_close_a_historical_sibling(
    db: AsyncSession, siblings: Siblings, child_case: ChildCase
) -> None:
    resumed = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            siblings.conversation.id,
            child_case.graph.agent_id,
            siblings.context,
            "chat",
            None,
            run_source="resume",
            parent_run_id=siblings.run.id,
            interrupt_id="approval-alpha",
        ),
    )
    await db.execute(
        update(ConversationChildTask)
        .where(ConversationChildTask.id == siblings.alpha)
        .values(
            current_run_id=resumed.run_id, status="completed", updated_at=siblings.run.completed_at
        )
    )
    await db.commit()
    # Keep the timestamp at the cutoff to isolate the physical-run condition.
    fresh = await readable_child_task_visibility(
        db, siblings.conversation, siblings.context, siblings.run.id
    )
    assert ("call-alpha", ALPHA, str(siblings.alpha)) in fresh.bindings
    assert await siblings.outcomes(db) == {BETA: "completed"}
