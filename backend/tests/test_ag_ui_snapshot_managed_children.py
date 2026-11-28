"""Recover private child approvals only from matching native and public identities."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_v1_children import public_subagent_id
from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.protocol_events import ProtocolInterrupt
from app.authz.org_context import OrgContext
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_read_access import load_child_visibility
from app.services.ag_ui_read_pending import CurrentReadInterrupts
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot
from app.services.ag_ui_snapshot_history import SnapshotHistory, SnapshotMessageRef
from app.services.ag_ui_snapshot_managed_children import restore_managed_children
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.tools.keys import DELEGATION_TOOL
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case
from tests.authz.test_child_task_read_bindings import CALL, NAMESPACE
from tests.authz.test_child_task_read_bindings import bound as bound

pytestmark = pytest.mark.asyncio

NATIVE = tuple(NAMESPACE.split("|"))
SIBLING_NS = (*NATIVE[:-1], "task:parallel")
ISSUER = "root-assistant"
PUBLIC = "run-public"
CHECKPOINT = "root-pending"
SLOT = "child-decision"
PRIVATE = "unpublished child brief and attached document"


@dataclass
class ChildSnapshot:
    conversation: Conversation
    run: ConversationRun
    task: ConversationChildTask
    context: OrgContext
    history: SnapshotHistory
    merged: AssembledSnapshot
    pending: CurrentReadInterrupts
    visibility: ChildVisibility

    async def restore(
        self, db: AsyncSession, *, public_id: str = PUBLIC
    ) -> dict[tuple[str, ...], str]:
        return await restore_managed_children(
            db,
            conversation=self.conversation,
            context=self.context,
            run=self.run,
            public_run_id=public_id,
            history=self.history,
            merged=self.merged,
            pending=self.pending,
            visibility=self.visibility,
            secret_values=(),
        )


def _interrupt(ns: tuple[str, ...], *, slot: str = SLOT) -> ProtocolInterrupt:
    return ProtocolInterrupt(id=slot, ns=list(ns), value={"type": "ask_user", "text": PRIVATE})


@pytest.fixture
async def pending_child(
    db: AsyncSession, bound: Conversation, child_case: ChildCase
) -> ChildSnapshot:
    context = child_case.graph.context
    run = await db.get(ConversationRun, child_case.run_id)
    task = await db.scalar(
        select(ConversationChildTask).where(ConversationChildTask.run_id == child_case.run_id)
    )
    assert run is not None and task is not None
    run.status, run.is_active, run.interrupt_id = "interrupted", False, SLOT
    task.status, task.brief, task.checkpoint_id = "awaiting_approval", PRIVATE, "child-checkpoint"
    await db.commit()
    visibility = await load_child_visibility(db, bound, context, run.id)
    assert visibility.bindings == {(CALL, NATIVE, str(task.id))}
    source = AIMessage(
        id=ISSUER,
        content="",
        tool_calls=[
            {
                "name": DELEGATION_TOOL,
                "id": CALL,
                "args": {"subagent_type": task.role, "brief": PRIVATE},
            }
        ],
    )
    ref = SnapshotMessageRef(source, 0, CHECKPOINT, None)
    history = SnapshotHistory(
        CHECKPOINT, (ref,), "checkpoint", (CHECKPOINT,), checkpoint_run_ids={CHECKPOINT: run.id}
    )
    public = {
        "id": ISSUER,
        "role": "assistant",
        "content": "",
        "toolCalls": [
            {
                "id": public_tool_call_id(PUBLIC, None, ISSUER, CALL),
                "type": "function",
                "function": {"name": DELEGATION_TOOL, "arguments": "{}"},
            }
        ],
    }
    link = AgUiSnapshotMessageLink(
        message_id=ISSUER,
        run_id=PUBLIC,
        physical_run_id=str(run.id),
        checkpoint_id=CHECKPOINT,
    )
    merged = AssembledSnapshot([public], [link], [], {}, ISSUER, set())
    pending = CurrentReadInterrupts(
        checkpoint_id=CHECKPOINT,
        interrupts=(_interrupt(NATIVE),),
        actionable=True,
    )
    return ChildSnapshot(bound, run, task, context, history, merged, pending, visibility)


async def test_current_enrolled_child_restores_its_public_issuer_and_protected_task(
    db: AsyncSession,
    pending_child: ChildSnapshot,
) -> None:
    case = pending_child
    expected = public_subagent_id(str(case.conversation.id), NATIVE)
    assert await case.restore(db) == {NATIVE: expected}
    # A tab can restore twice; its single public child must keep exactly one durable handle.
    assert await case.restore(db) == {NATIVE: expected}
    [child] = case.merged.subagents
    assert child["subagentRunId"] == expected
    assert child["childTaskId"] == str(case.task.id)
    assert child["parentMessageId"] == ISSUER
    assert child["parentToolCallId"] == public_tool_call_id(PUBLIC, None, ISSUER, CALL)
    assert (child["runId"], child["physicalRunId"]) == (PUBLIC, str(case.run.id))
    assert child["status"] == "interrupted"
    assert PRIVATE not in repr((child, case.merged.messages))


@pytest.mark.parametrize(
    "mismatch",
    ["revoked", "closed", "wrong-task", "parallel", "different-role", "not-actionable"],
)
async def test_changed_proofs_never_restore_a_child_or_attach_the_pending_approval(
    db: AsyncSession,
    pending_child: ChildSnapshot,
    mismatch: str,
) -> None:
    case = pending_child
    if mismatch == "revoked":
        await db.execute(
            delete(AgentSubAgentLink).where(
                AgentSubAgentLink.parent_agent_id == case.run.agent_id,
                AgentSubAgentLink.sub_agent_id == case.task.child_agent_id,
            )
        )
        await db.commit()
        latest = await load_child_visibility(db, case.conversation, case.context, case.run.id)
        assert case.visibility.bindings and not latest.bindings
    elif mismatch == "closed":
        case.task.status = "completed"
        await db.commit()
    elif mismatch == "wrong-task":
        case.visibility = replace(
            case.visibility, bindings=frozenset({(CALL, NATIVE, str(uuid4()))})
        )
    elif mismatch == "parallel":
        case.pending = replace(
            case.pending, interrupts=(_interrupt((*NATIVE[:-1], "task:parallel")),)
        )
    elif mismatch == "different-role":
        case.task.role = "another-role"
        await db.commit()
    elif mismatch == "not-actionable":
        case.pending = replace(case.pending, actionable=False)

    assert await case.restore(db) == {}
    assert case.merged.subagents == []


async def test_deeper_approval_uses_the_registered_child_not_the_internal_tool_namespace(
    db: AsyncSession,
    pending_child: ChildSnapshot,
) -> None:
    case = pending_child
    approval_namespace = (*NATIVE, "workflow:review")
    case.pending = replace(case.pending, interrupts=(_interrupt(approval_namespace),))

    child_id = public_subagent_id(str(case.conversation.id), NATIVE)
    assert await case.restore(db) == {approval_namespace: child_id}
    assert [row["subagentRunId"] for row in case.merged.subagents] == [child_id]


async def test_resumed_approval_belongs_to_current_run_and_the_original_parent_call(
    db: AsyncSession,
    pending_child: ChildSnapshot,
) -> None:
    case = pending_child
    original = case.run
    assert original.agent_id is not None
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            case.conversation.id,
            original.agent_id,
            case.context,
            "chat",
            None,
            run_source="resume",
            parent_run_id=original.id,
            interrupt_id=SLOT,
        ),
    )
    resumed = await db.get(ConversationRun, accepted.run_id)
    assert resumed is not None
    case.task.current_run_id = resumed.id
    resumed.status, resumed.is_active, resumed.interrupt_id = "interrupted", False, "next-slot"
    await db.commit()
    case.run = resumed
    case.history = replace(
        case.history,
        checkpoint_id="resumed-head",
        lineage_checkpoint_ids=(CHECKPOINT, "resumed-head"),
        checkpoint_run_ids={CHECKPOINT: original.id, "resumed-head": resumed.id},
    )
    case.pending = replace(
        case.pending,
        checkpoint_id="resumed-head",
        interrupts=(_interrupt(NATIVE, slot="next-slot"),),
    )
    case.visibility = await load_child_visibility(db, case.conversation, case.context, resumed.id)
    assert case.visibility.bindings == {(CALL, NATIVE, str(case.task.id))}
    child_id = public_subagent_id(str(case.conversation.id), NATIVE)

    assert await case.restore(db, public_id="resumed-public") == {NATIVE: child_id}
    [child] = case.merged.subagents
    assert child["parentToolCallId"] == public_tool_call_id(PUBLIC, None, ISSUER, CALL)
    assert (child["runId"], child["physicalRunId"]) == ("resumed-public", str(resumed.id))


@pytest.fixture
async def parallel_child(
    db: AsyncSession,
    pending_child: ChildSnapshot,
) -> ConversationChildTask:
    case = pending_child
    sibling = ConversationChildTask(
        run_id=case.run.id,
        current_run_id=case.run.id,
        child_agent_id=case.task.child_agent_id,
        tool_call_id="call-second",
        role=case.task.role,
        checkpoint_ns="|".join(SIBLING_NS),
        checkpoint_id="parallel-checkpoint",
        status="awaiting_approval",
    )
    db.add(sibling)
    await db.commit()
    [ref] = case.history.messages
    source = ref.message
    assert isinstance(source, AIMessage)
    source.tool_calls.append(
        {
            "id": sibling.tool_call_id,
            "name": DELEGATION_TOOL,
            "args": {"subagent_type": sibling.role},
        }
    )
    case.merged.messages[0]["toolCalls"].append(
        {
            "id": public_tool_call_id(PUBLIC, None, ISSUER, sibling.tool_call_id),
            "type": "function",
            "function": {"name": DELEGATION_TOOL, "arguments": "{}"},
        }
    )
    case.visibility = await load_child_visibility(db, case.conversation, case.context, case.run.id)
    assert len(case.visibility.bindings) == 2
    return sibling


async def test_parallel_child_approvals_follow_each_issuance_regardless_of_slot_order(
    db: AsyncSession,
    pending_child: ChildSnapshot,
    parallel_child: ConversationChildTask,
) -> None:
    case = pending_child
    case.pending = replace(
        case.pending,
        interrupts=(
            _interrupt(SIBLING_NS, slot="parallel-approval"),
            _interrupt(NATIVE),
        ),
    )
    first_id = public_subagent_id(str(case.conversation.id), NATIVE)
    second_id = public_subagent_id(str(case.conversation.id), SIBLING_NS)
    assert await case.restore(db) == {NATIVE: first_id, SIBLING_NS: second_id}
    controls = {
        row["subagentRunId"]: (row["childTaskId"], row["parentToolCallId"])
        for row in case.merged.subagents
    }
    assert controls == {
        first_id: (str(case.task.id), public_tool_call_id(PUBLIC, None, ISSUER, CALL)),
        second_id: (
            str(parallel_child.id),
            public_tool_call_id(PUBLIC, None, ISSUER, parallel_child.tool_call_id),
        ),
    }


@pytest.mark.parametrize("visible", ["both", "waiting-only", "completed-only"])
async def test_same_role_completed_sibling_cannot_acquire_the_current_approval(
    db: AsyncSession,
    pending_child: ChildSnapshot,
    parallel_child: ConversationChildTask,
    visible: str,
) -> None:
    case = pending_child
    parallel_child.status = "completed"
    await db.commit()
    if visible != "both":
        allowed = case.task.id if visible == "waiting-only" else parallel_child.id
        case.visibility = replace(
            case.visibility,
            bindings=frozenset(row for row in case.visibility.bindings if row[2] == str(allowed)),
        )
    restored = await case.restore(db)
    if visible == "completed-only":
        assert restored == {}
        assert case.merged.subagents == []
        return
    waiting_id = public_subagent_id(str(case.conversation.id), NATIVE)
    assert restored == {NATIVE: waiting_id}
    [child] = case.merged.subagents
    assert child["childTaskId"] == str(case.task.id)
    assert child["parentToolCallId"] == public_tool_call_id(PUBLIC, None, ISSUER, CALL)


async def test_disappearing_nested_task_cannot_transfer_its_approval_to_its_parent(
    db: AsyncSession,
    pending_child: ChildSnapshot,
) -> None:
    case = pending_child
    nested_ns = (*NATIVE, "tools:child-approval")
    nested = ConversationChildTask(
        run_id=case.run.id,
        current_run_id=case.run.id,
        child_agent_id=case.task.child_agent_id,
        tool_call_id="nested-approval",
        role="review",
        checkpoint_ns="|".join(nested_ns),
        checkpoint_id="nested-pending",
        status="awaiting_approval",
    )
    db.add(nested)
    await db.commit()
    case.visibility = await load_child_visibility(db, case.conversation, case.context, case.run.id)
    assert case.visibility.namespaces == {NATIVE, nested_ns}
    case.pending = replace(case.pending, interrupts=(_interrupt(nested_ns),))

    await db.delete(nested)
    await db.commit()
    fresh = await load_child_visibility(db, case.conversation, case.context, case.run.id)
    assert fresh.namespaces == {NATIVE}

    assert await case.restore(db) == {}
    assert case.merged.subagents == []


async def test_expired_approval_recovers_the_same_owner_without_its_control_handle(
    db: AsyncSession,
    pending_child: ChildSnapshot,
) -> None:
    case = pending_child
    child_id = public_subagent_id(str(case.conversation.id), NATIVE)
    assert await case.restore(db) == {NATIVE: child_id}
    [before] = case.merged.subagents
    assert before["childTaskId"] == str(case.task.id)

    case.pending = replace(
        case.pending,
        actionable=False,
        interrupts=(
            ProtocolInterrupt(
                id=SLOT,
                ns=list(NATIVE),
                value={
                    "action_requests": [{"name": "send", "args": {"body": PRIVATE}}],
                    "review_configs": [
                        {"action_name": "send", "allowed_decisions": ["approve", "reject"]}
                    ],
                },
            ),
        ),
        expires_at={SLOT: "2000-01-01T00:00:00+00:00"},
    )
    assert await case.restore(db) == {NATIVE: child_id}
    [expired] = case.merged.subagents
    assert expired["subagentRunId"] == child_id
    assert expired["parentMessageId"] == ISSUER
    assert expired["parentToolCallId"] == public_tool_call_id(PUBLIC, None, ISSUER, CALL)
    assert not expired.get("childTaskId")
    assert PRIVATE not in repr(expired)


async def test_expiration_does_not_revive_a_denied_approval_owner(
    db: AsyncSession,
    pending_child: ChildSnapshot,
) -> None:
    case = pending_child
    case.pending = replace(
        case.pending,
        actionable=False,
        child_authority_denied=True,
        expires_at={SLOT: "2000-01-01T00:00:00+00:00"},
    )

    assert await case.restore(db) == {}
    assert case.merged.subagents == []
