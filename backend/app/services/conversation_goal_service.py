"""Versioned goals, run receipts and follow-up reservations for private chat.

Every writer holds the conversation lock before accessing the goal. The normal
execution gateway obtains it after tenant, membership and agent locks. State,
evidence and notification intent are committed by the caller in one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.emergency import refuse_run
from app.authz.org_context import OrgContext
from app.config import settings
from app.exceptions import AppError, ConflictError
from app.models.conversation import Conversation
from app.models.conversation_goal import ConversationGoal, GoalAttempt, GoalTransitionIntent
from app.models.conversation_run import RUN_TERMINAL_STATUSES, ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_service
from app.tenancy.limits import Subject, UsageLimits

if TYPE_CHECKING:
    from app.schemas.conversation_goal import GoalActionRequest, GoalWriteRequest

MAX_FOLLOWUPS = 3
MAX_NO_PROGRESS = 3


@dataclass(frozen=True)
class GoalSteerState:
    latest_received: int
    latest_applied: int
    has_unresolved: bool


async def _watermarks(
    db: AsyncSession, conversation_id: UUID, after_sequence: int = 0
) -> GoalSteerState:
    from app.services.conversation_run_steers import get_conversation_steer_watermarks

    state = await get_conversation_steer_watermarks(
        db, conversation_id, after_sequence=after_sequence
    )
    return GoalSteerState(state.latest_received, state.latest_applied, state.has_unresolved)


def goal_enabled(org_id: UUID) -> bool:
    return org_id in settings.conversation_goal_org_ids


def ordinary_conversation(conversation: Conversation) -> bool:
    return conversation.source in {"ui", "draft"} and conversation.side_chat_parent_id is None


def _context(conversation: Conversation) -> OrgContext:
    return OrgContext(conversation.org_id, conversation.tenant_id, conversation.user_id)


def _conflict(code: str) -> ConflictError:
    return ConflictError(code, code)


async def get_goal(db: AsyncSession, conversation: Conversation) -> ConversationGoal | None:
    return await db.scalar(
        select(ConversationGoal).where(
            ConversationGoal.conversation_id == conversation.id,
            ConversationGoal.org_id == conversation.org_id,
            ConversationGoal.tenant_id == conversation.tenant_id,
            ConversationGoal.user_id == conversation.user_id,
        )
    )


def _check_edit(conversation: Conversation, goal: ConversationGoal | None, revision: int) -> None:
    if not ordinary_conversation(conversation) or not goal_enabled(conversation.org_id):
        raise _conflict("goal_disabled")
    if (goal.revision if goal else 0) != revision:
        raise _conflict("goal_revision_conflict")


async def _invalidate_attempts(db: AsyncSession, goal: ConversationGoal) -> None:
    reservations = select(GoalAttempt.followup_input_id).where(
        GoalAttempt.goal_id == goal.id, GoalAttempt.status == "followup_reserved"
    )
    await db.execute(
        update(ConversationRunInput)
        .where(
            ConversationRunInput.id.in_(reservations),
            ConversationRunInput.source == "goal_followup",
            ConversationRunInput.status == "pending",
        )
        .values(status="canceled", revision=ConversationRunInput.revision + 1)
    )
    await db.execute(
        update(GoalAttempt)
        .where(
            GoalAttempt.goal_id == goal.id, GoalAttempt.status.in_(("pending", "followup_reserved"))
        )
        .values(status="superseded")
    )


async def save_goal(
    db: AsyncSession, conversation: Conversation, payload: GoalWriteRequest
) -> ConversationGoal:
    goal = await get_goal(db, conversation)
    _check_edit(conversation, goal, payload.expected_revision)
    criteria = [item.model_dump(mode="json") for item in payload.criteria]
    if goal and goal.title == payload.title and goal.criteria == criteria:
        return goal
    from app.schemas.conversation_scope import ConversationPromotion
    from app.services.chat.conversations import promote_draft_conversation

    marks = await _watermarks(db, conversation.id)
    seq = marks.latest_received
    if conversation.source == "draft":
        await promote_draft_conversation(
            db, ConversationPromotion(conversation.id, _context(conversation), payload.title)
        )
    # The owner may accept new conditions mid-run. Reconcile that predecessor
    # exactly once, solely to start work with fresh conditions at its terminal commit.
    predecessor = await _target_predecessor(db, conversation, goal)
    if goal is None:
        goal = ConversationGoal(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            org_id=conversation.org_id,
            tenant_id=conversation.tenant_id,
            version=1,
            revision=1,
            baseline_steer_seq=seq if predecessor is None else marks.latest_applied,
        )
        db.add(goal)
    else:
        await _invalidate_attempts(db, goal)
        goal.revision += 1
        goal.version += 1
    goal.title = payload.title
    goal.criteria = criteria
    goal.status = "active"
    goal.auto_followups = 0
    goal.no_progress_count = 0
    goal.latest_steer_seq = seq
    goal.last_evidence = None
    goal.blocked_reason = None
    await db.flush()
    if predecessor is not None:
        receipt = GoalAttempt(
            goal_id=goal.id,
            goal_version=goal.version,
            run_id=predecessor.id,
            steer_seq=seq,
            status="pending",
            requires_fresh_run=True,
        )
        db.add(receipt)
        await db.flush()
        predecessor.metadata_json = {
            **(predecessor.metadata_json or {}),
            "goal_attempt_id": str(receipt.id),
        }
        await db.flush()
    return goal


async def _target_predecessor(
    db: AsyncSession, conversation: Conversation, goal: ConversationGoal | None
) -> ConversationRun | None:
    query = select(ConversationRun).where(
        ConversationRun.conversation_id == conversation.id,
        ConversationRun.user_id == conversation.user_id,
        ConversationRun.org_id == conversation.org_id,
        ConversationRun.tenant_id == conversation.tenant_id,
        ConversationRun.run_source == "CHAT",
        ConversationRun.invoker_type == "user",
        ConversationRun.source.in_(("chat", "start", "resume")),
    )
    active = await db.scalar(query.where(ConversationRun.is_active.is_(True)))
    if active or not goal or goal.status != "active":
        return active
    return await db.scalar(
        query.join(GoalAttempt, GoalAttempt.run_id == ConversationRun.id)
        .where(
            GoalAttempt.goal_id == goal.id,
            GoalAttempt.goal_version == goal.version,
            GoalAttempt.status.in_(("pending", "followup_reserved")),
        )
        .order_by(ConversationRun.created_at.desc(), ConversationRun.id.desc())
        .limit(1)
    )


async def _check_admission(db: AsyncSession, conversation: Conversation) -> None:
    agent_id = conversation.agent_id
    if agent_id is None:
        raise _conflict("goal_agent_unavailable")
    await refuse_run(db, tenant_id=conversation.tenant_id, agent_id=agent_id)
    await UsageLimits.check(
        db, Subject(conversation.tenant_id, conversation.org_id, conversation.user_id, agent_id)
    )
    if conversation.queue_paused:
        raise _conflict("queue_paused")
    if await conversation_run_service.get_latest_interrupted_run(
        db, conversation_id=conversation.id, context=_context(conversation)
    ):
        raise _conflict("approval_pending")


async def apply_goal_action(
    db: AsyncSession, conversation: Conversation, payload: GoalActionRequest
) -> ConversationGoal:
    goal = await get_goal(db, conversation)
    _check_edit(conversation, goal, payload.expected_revision)
    if goal is None or goal.status in {"completed", "canceled"}:
        raise _conflict("goal_action_unavailable")
    if payload.action == "resume":
        if goal.status not in {"paused", "blocked"}:
            raise _conflict("goal_action_unavailable")
        await _check_admission(db, conversation)
        marks = await _watermarks(db, conversation.id, goal.baseline_steer_seq)
        if marks.has_unresolved:
            raise _conflict("steer_pending")
        goal.latest_steer_seq = marks.latest_received
        goal.status = "active"
        await _resume_work(db, conversation, goal)
    else:
        if payload.action == "pause" and goal.status != "active":
            raise _conflict("goal_action_unavailable")
        await _invalidate_attempts(db, goal)
        goal.status = "paused" if payload.action == "pause" else "canceled"
    goal.blocked_reason = None
    goal.revision += 1
    await db.flush()
    return goal


async def _resume_work(
    db: AsyncSession, conversation: Conversation, goal: ConversationGoal
) -> None:
    """Continue previously admitted work after an explicit owner resume."""
    from app.services.conversation_goal_worker import enqueue_reserved_followup

    if await _user_input_waits(db, conversation):
        if goal.auto_followups >= MAX_FOLLOWUPS:
            raise _conflict("followup_limit")
        return
    active = await conversation_run_service.get_active_run(
        db, conversation_id=conversation.id, context=_context(conversation)
    )
    if active is not None:
        if goal.auto_followups >= MAX_FOLLOWUPS:
            raise _conflict("followup_limit")
        if (
            active.run_source != "CHAT"
            or active.invoker_type != "user"
            or active.source not in {"chat", "resume", "start"}
        ):
            raise _conflict("goal_run_ineligible")
        receipt = await db.scalar(
            select(GoalAttempt).where(
                GoalAttempt.goal_id == goal.id,
                GoalAttempt.goal_version == goal.version,
                GoalAttempt.run_id == active.id,
            )
        )
        if receipt is None:
            receipt = GoalAttempt(goal_id=goal.id, goal_version=goal.version, run_id=active.id)
            db.add(receipt)
        receipt.status = "pending"
        receipt.steer_seq = goal.latest_steer_seq
        receipt.requires_fresh_run = True
        await db.flush()
        active.metadata_json = {**(active.metadata_json or {}), "goal_attempt_id": str(receipt.id)}
        return
    # A never-started goal will bind to its first normal user turn.
    last = await db.scalar(
        select(GoalAttempt)
        .join(ConversationRun, ConversationRun.id == GoalAttempt.run_id)
        .where(
            GoalAttempt.goal_id == goal.id,
            ConversationRun.conversation_id == conversation.id,
            ConversationRun.status.in_(RUN_TERMINAL_STATUSES),
        )
        .order_by(ConversationRun.created_at.desc(), GoalAttempt.goal_version.desc())
        .limit(1)
    )
    if last is None:
        if goal.auto_followups >= MAX_FOLLOWUPS:
            raise _conflict("followup_limit")
        return
    latest_owner_run = await db.scalar(
        select(ConversationRun.id)
        .where(
            ConversationRun.conversation_id == conversation.id,
            ConversationRun.user_id == conversation.user_id,
            ConversationRun.org_id == conversation.org_id,
            ConversationRun.tenant_id == conversation.tenant_id,
            ConversationRun.run_source == "CHAT",
            ConversationRun.invoker_type == "user",
            ConversationRun.source.in_(("chat", "start", "edit", "regenerate", "resume")),
            ConversationRun.status.in_(RUN_TERMINAL_STATUSES),
        )
        .order_by(ConversationRun.created_at.desc(), ConversationRun.id.desc())
        .limit(1)
    )
    if latest_owner_run is None:
        raise _conflict("goal_run_ineligible")
    if last.goal_version != goal.version or last.run_id != latest_owner_run:
        last = GoalAttempt(goal_id=goal.id, goal_version=goal.version, run_id=latest_owner_run)
        db.add(last)
    last.steer_seq = goal.latest_steer_seq
    last.requires_fresh_run = True
    if last.followup_input_id:
        queued = await db.get(ConversationRunInput, last.followup_input_id)
        if queued is None or queued.status != "canceled" or queued.run_id is not None:
            raise _conflict("goal_followup_already_claimed")
        queued.status = "pending"
        queued.revision += 1
        last.status = "followup_reserved"
        return
    if goal.auto_followups >= MAX_FOLLOWUPS:
        raise _conflict("followup_limit")
    goal.auto_followups += 1
    last.followup_ordinal = goal.auto_followups
    last.status = "followup_reserved"
    await db.flush()
    await enqueue_reserved_followup(db, conversation, goal, last)


async def pause_goal_for_history_change(db: AsyncSession, conversation: Conversation) -> None:
    if not ordinary_conversation(conversation):
        return
    goal = await get_goal(db, conversation)
    if goal is None or goal.status == "canceled":
        return
    await _invalidate_attempts(db, goal)
    # Old checks must never carry into another history, including a branch
    # selection made while a user-paused goal is waiting to be resumed.
    goal.version += 1
    goal.status = "paused"
    goal.blocked_reason = "history_changed"
    goal.last_evidence = None
    goal.revision += 1
    await db.flush()


def _same_run_scope(conversation: Conversation, run: ConversationRun) -> bool:
    return (
        run.conversation_id == conversation.id
        and run.org_id == conversation.org_id
        and run.tenant_id == conversation.tenant_id
        and run.user_id == conversation.user_id
    )


async def capture_goal_attempt(
    db: AsyncSession, *, conversation: Conversation, run: ConversationRun
) -> GoalAttempt | None:
    if not (
        ordinary_conversation(conversation)
        and goal_enabled(conversation.org_id)
        and _same_run_scope(conversation, run)
        and run.run_source == "CHAT"
        and run.source in {"chat", "start", "resume"}
    ):
        return None
    goal = await get_goal(db, conversation)
    if goal is None:
        return None
    if run.source == "resume":
        parent = await db.scalar(
            select(GoalAttempt).where(
                GoalAttempt.goal_id == goal.id,
                GoalAttempt.goal_version == goal.version,
                GoalAttempt.run_id == run.parent_run_id,
                GoalAttempt.status.in_(("blocked", "pending")),
            )
        )
        if parent is None:
            return None
        if goal.status == "blocked" and goal.blocked_reason == "approval_pending":
            goal.status = "active"
            goal.blocked_reason = None
            goal.revision += 1
    if goal.status != "active":
        return None
    existing = await db.scalar(
        select(GoalAttempt).where(
            GoalAttempt.goal_id == goal.id,
            GoalAttempt.goal_version == goal.version,
            GoalAttempt.run_id == run.id,
        )
    )
    if existing:
        return existing
    # The terminal callback can be recovered after a newer owner turn has already
    # claimed the conversation. Only the most recent admitted attempt may decide it.
    await db.execute(
        update(GoalAttempt)
        .where(
            GoalAttempt.goal_id == goal.id,
            GoalAttempt.goal_version == goal.version,
            GoalAttempt.status == "pending",
        )
        .values(status="superseded")
    )
    state = await _watermarks(db, conversation.id, goal.baseline_steer_seq)
    goal.latest_steer_seq = state.latest_received
    attempt = GoalAttempt(
        goal_id=goal.id,
        goal_version=goal.version,
        run_id=run.id,
        steer_seq=state.latest_received,
        status="pending",
    )
    db.add(attempt)
    await db.flush()
    return attempt


def _intent(
    db: AsyncSession,
    goal: ConversationGoal,
    attempt: GoalAttempt,
    kind: str,
    interrupt_id: str | None = None,
) -> None:
    db.add(
        GoalTransitionIntent(
            goal_id=goal.id,
            goal_version=goal.version,
            goal_revision=goal.revision,
            run_id=attempt.run_id,
            kind=kind,
            payload={
                "conversation_id": str(goal.conversation_id),
                "goal_id": str(goal.id),
                "goal_version": goal.version,
                "goal_revision": goal.revision,
                "run_id": str(attempt.run_id),
                "blocked_reason": goal.blocked_reason,
                "interrupt_id": interrupt_id,
                "evidence": goal.last_evidence,
            },
        )
    )


async def block_goal(
    db: AsyncSession,
    goal: ConversationGoal,
    reason: str,
    *,
    attempt: GoalAttempt | None = None,
    interrupt_id: str | None = None,
) -> None:
    if goal.status != "active":
        return
    goal.status = "blocked"
    goal.blocked_reason = reason
    goal.revision += 1
    if attempt:
        attempt.status = "blocked"
        _intent(
            db,
            goal,
            attempt,
            "approval_requested" if reason == "approval_pending" else "blocked",
            interrupt_id,
        )
    await db.flush()


async def _user_input_waits(db: AsyncSession, conversation: Conversation) -> bool:
    return bool(
        await db.scalar(
            select(ConversationRunInput.id)
            .where(
                ConversationRunInput.conversation_id == conversation.id,
                ConversationRunInput.user_id == conversation.user_id,
                ConversationRunInput.status == "pending",
                ConversationRunInput.source != "goal_followup",
            )
            .limit(1)
        )
    )


async def evaluate_goal_attempt(
    db: AsyncSession,
    *,
    conversation: Conversation,
    run: ConversationRun,
    context: OrgContext | None = None,
) -> tuple[ConversationGoal, GoalAttempt] | None:
    """Settle one committed terminal run while holding the authorized conversation lock."""
    from app.services.conversation_goal_evidence import inspect_goal_evidence

    if (
        not _same_run_scope(conversation, run)
        or run.is_active
        or run.status not in RUN_TERMINAL_STATUSES
    ):
        return None
    goal = await get_goal(db, conversation)
    if goal is None or goal.status != "active":
        return None
    attempt = await db.scalar(
        select(GoalAttempt).where(
            GoalAttempt.goal_id == goal.id,
            GoalAttempt.goal_version == goal.version,
            GoalAttempt.run_id == run.id,
        )
    )
    if attempt is None or attempt.status not in {"pending", "followup_reserved"}:
        return None
    if attempt.status == "followup_reserved":
        return goal, attempt

    marks = await _watermarks(db, conversation.id, goal.baseline_steer_seq)
    goal.latest_steer_seq = marks.latest_received
    reason = None
    if not goal_enabled(conversation.org_id):
        reason = "feature_disabled"
    elif run.status != "completed":
        reason = "approval_pending" if run.status == "interrupted" else "run_" + run.status
    elif marks.has_unresolved:
        reason = "steer_pending"
    elif await _user_input_waits(db, conversation):
        # A real user turn owns the next judgement. Keep the goal active.
        attempt.status = "superseded"
        await db.flush()
        return None
    if reason:
        await block_goal(db, goal, reason, attempt=attempt, interrupt_id=run.interrupt_id)
        return None
    if attempt.requires_fresh_run or attempt.steer_seq != marks.latest_received:
        # The current run may have written its file before the owner's change.
        # Its content cannot prove the new target; the next bound run must produce it.
        attempt.steer_seq = marks.latest_received
        attempt.requires_fresh_run = True
        goal.last_evidence = None
    else:
        previous = await db.scalar(
            select(GoalAttempt.evidence)
            .where(
                GoalAttempt.goal_id == goal.id,
                GoalAttempt.goal_version == goal.version,
                GoalAttempt.steer_seq == attempt.steer_seq,
                GoalAttempt.id != attempt.id,
                GoalAttempt.evidence.is_not(None),
            )
            .order_by(GoalAttempt.created_at.desc(), GoalAttempt.id.desc())
            .limit(1)
        )
        try:
            evidence = await inspect_goal_evidence(
                db,
                conversation=conversation,
                run=run,
                criteria=goal.criteria,
                previous_evidence=previous,
            )
        except AppError:
            await block_goal(db, goal, "permission_revoked", attempt=attempt)
            return None
        attempt.evidence = evidence.model_dump(mode="json")
        goal.last_evidence = attempt.evidence
        if evidence.all_passed:
            goal.status = "completed"
            goal.revision += 1
            attempt.status = "completed"
            _intent(db, goal, attempt, "completed")
            await db.flush()
            return None
        prior_progress = (previous or {}).get("progress", 0)
        goal.no_progress_count = (
            0 if evidence.progress > prior_progress else goal.no_progress_count + 1
        )
    if any(c.get("kind") == "manual" for c in goal.criteria):
        reason = "manual_review"
    elif goal.no_progress_count >= MAX_NO_PROGRESS:
        reason = "no_progress"
    elif goal.auto_followups >= MAX_FOLLOWUPS:
        reason = "followup_limit"
    else:
        try:
            await _check_admission(db, conversation)
        except AppError as exc:
            reason = (
                exc.code
                if exc.code in {"approval_pending", "queue_paused"}
                else "usage_limit"
                if exc.status == 429
                else "policy_blocked"
            )
    if reason:
        await block_goal(db, goal, reason, attempt=attempt)
        return None
    goal.auto_followups += 1
    goal.revision += 1
    attempt.followup_ordinal = goal.auto_followups
    attempt.status = "followup_reserved"
    await db.flush()
    return goal, attempt


async def _reservation(
    db: AsyncSession, conversation: Conversation, input_id: UUID
) -> tuple[ConversationGoal, GoalAttempt] | None:
    result = (
        await db.execute(
            select(ConversationGoal, GoalAttempt)
            .join(GoalAttempt, GoalAttempt.goal_id == ConversationGoal.id)
            .where(
                ConversationGoal.conversation_id == conversation.id,
                ConversationGoal.user_id == conversation.user_id,
                ConversationGoal.org_id == conversation.org_id,
                ConversationGoal.tenant_id == conversation.tenant_id,
                ConversationGoal.status == "active",
                GoalAttempt.goal_version == ConversationGoal.version,
                GoalAttempt.status == "followup_reserved",
                GoalAttempt.followup_input_id == input_id,
            )
        )
    ).one_or_none()
    return None if result is None else (result[0], result[1])


async def verify_goal_followup(
    db: AsyncSession, *, conversation: Conversation, input_id: UUID
) -> bool:
    reservation = await _reservation(db, conversation, input_id)
    if reservation is None:
        return False
    goal, attempt = reservation
    if not goal_enabled(conversation.org_id) or not ordinary_conversation(conversation):
        await block_goal(db, goal, "feature_disabled", attempt=attempt)
        return False
    state = await _watermarks(db, conversation.id, goal.baseline_steer_seq)
    if state.has_unresolved or state.latest_received != attempt.steer_seq:
        await block_goal(
            db, goal, "steer_pending" if state.has_unresolved else "steer_changed", attempt=attempt
        )
        return False
    if await _user_input_waits(db, conversation):
        attempt.status = "superseded"
        await db.flush()
        return False
    return True


async def block_goal_followup(
    db: AsyncSession, *, conversation: Conversation, input_id: UUID, reason: str
) -> None:
    reservation = await _reservation(db, conversation, input_id)
    if reservation:
        goal, attempt = reservation
        await block_goal(db, goal, reason, attempt=attempt)
