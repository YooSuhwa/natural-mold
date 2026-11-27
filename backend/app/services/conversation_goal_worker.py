"""Bind ordinary chat attempts and dispatch durable, restricted goal continuations."""

from __future__ import annotations

import json
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime.goal_run import goal_setup_allowed
from app.agent_runtime.runtime_config import AgentConfig
from app.approvals.emergency import refuse_run
from app.authz.conversation_agent_checks import queue_dispatch_denial
from app.authz.conversation_input_scope import owned_input_predicate
from app.authz.conversation_scope import resolve_conversation_actor
from app.authz.dispatch_block import TRANSIENT, block_reason
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate, same_private_scope
from app.authz.worker_conversation_scope import goal_reservation_owner_predicate
from app.config import settings
from app.database import SessionTenant
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_goal import ConversationGoal, GoalAttempt
from app.models.conversation_run import RUN_TERMINAL_STATUSES, ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services.conversation_goal_service import (
    block_goal,
    block_goal_followup,
    capture_goal_attempt,
    evaluate_goal_attempt,
    goal_enabled,
    ordinary_conversation,
    pause_goal_for_history_change,
    verify_goal_followup,
)
from app.services.conversation_run_queue_mutations import require_queue_executable
from app.tenancy.limits import Subject, UsageLimits

logger = logging.getLogger(__name__)
GOAL_INPUT_SOURCE = "goal_followup"
ATTEMPT_KEY = "goal_attempt_id"
INPUT_KEY = "goal_followup_input_id"


def _goal_context(goal: ConversationGoal) -> str:
    return json.dumps(
        {"goal": goal.title, "version": goal.version, "completion_criteria": goal.criteria},
        ensure_ascii=False,
    )


async def capture_new_run(
    db: AsyncSession,
    *,
    conversation: Conversation,
    run: ConversationRun,
    interrupted_parent: ConversationRun | None,
) -> None:
    """Only the server may stamp the attempt and the restricted followup identity."""
    metadata = dict(run.metadata_json or {})
    for key in (ATTEMPT_KEY, INPUT_KEY):
        metadata.pop(key, None)
    if interrupted_parent is not None:
        parent_input = (interrupted_parent.metadata_json or {}).get(INPUT_KEY)
        if parent_input:
            metadata[INPUT_KEY] = parent_input
    if (
        run.run_source == "CHAT"
        and run.invoker_type == "user"
        and ordinary_conversation(conversation)
    ):
        if run.source in {"edit", "regenerate"}:
            await pause_goal_for_history_change(db, conversation)
        elif run.source in {"chat", "start", "resume"} and goal_enabled(conversation.org_id):
            attempt = await capture_goal_attempt(db, conversation=conversation, run=run)
            if attempt is not None:
                metadata[ATTEMPT_KEY] = str(attempt.id)
                if run.source != "resume":
                    await _supersede_older_queue_items(
                        db, conversation, metadata.get("queue_input_id")
                    )
    if metadata != (run.metadata_json or {}):
        run.metadata_json = metadata or None


async def _supersede_older_queue_items(
    db: AsyncSession, conversation: Conversation, claiming_id: str | None
) -> None:
    # A newer owner turn takes precedence over a reservation even when it was accepted
    # in the gap between evaluation and the worker's next dispatch.
    query = select(ConversationRunInput.id).where(
        ConversationRunInput.conversation_id == conversation.id,
        owned_input_predicate(conversation),
        ConversationRunInput.source == GOAL_INPUT_SOURCE,
        ConversationRunInput.status == "pending",
    )
    if claiming_id is not None:
        query = query.where(ConversationRunInput.id != UUID(claiming_id))
    ids = list(await db.scalars(query))
    if ids:
        await db.execute(
            update(GoalAttempt)
            .where(
                GoalAttempt.followup_input_id.in_(ids), GoalAttempt.status == "followup_reserved"
            )
            .values(status="superseded")
        )
        await db.execute(
            update(ConversationRunInput)
            .where(ConversationRunInput.id.in_(ids))
            .values(status="canceled", revision=ConversationRunInput.revision + 1)
        )


async def configure_goal_runtime(db: AsyncSession, run: ConversationRun, cfg: AgentConfig) -> None:
    metadata = run.metadata_json or {}
    # On an approval resume the secured tool policy persists even if the goal was disabled.
    cfg.goal_followup = bool(metadata.get(INPUT_KEY))
    cfg.goal_context = None
    attempt = metadata.get(ATTEMPT_KEY)
    if not attempt or not goal_enabled(cfg.org_context.org_id):
        return
    goal = await db.scalar(
        select(ConversationGoal)
        .join(GoalAttempt, GoalAttempt.goal_id == ConversationGoal.id)
        .where(
            GoalAttempt.id == UUID(attempt),
            GoalAttempt.run_id == run.id,
            GoalAttempt.goal_version == ConversationGoal.version,
            ConversationGoal.conversation_id == run.conversation_id,
            private_scope_predicate(ConversationGoal, cfg.org_context),
            ConversationGoal.status == "active",
        )
    )
    if goal is not None:
        cfg.goal_context = _goal_context(goal)


async def enqueue_reserved_followup(
    db: AsyncSession, conversation: Conversation, goal: ConversationGoal, attempt: GoalAttempt
) -> None:
    """The conversation lock serializes the decision, queue insert and receipt in one commit."""
    if attempt.followup_input_id is not None or attempt.followup_ordinal is None:
        return
    last_position = await db.scalar(
        select(func.max(ConversationRunInput.position)).where(
            ConversationRunInput.conversation_id == conversation.id
        )
    )
    queued = ConversationRunInput(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        # The delivery receipt uses a server-assigned UUID, independent of user request IDs.
        client_request_id=str(uuid4()),
        status="pending",
        source=GOAL_INPUT_SOURCE,
        position=(last_position or 0) + 1,
        priority=-1,
        revision=1,
        input_payload={"messages": [{"role": "user", "content": _goal_context(goal)}]},
        attachment_ids=[],
    )
    db.add(queued)
    await db.flush()
    attempt.followup_input_id = queued.id


async def reconcile_goal_run(db: AsyncSession, run_id: UUID, conversation_id: UUID) -> None:
    conversation = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    if conversation is None:
        return
    run = await db.scalar(
        select(ConversationRun).where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation.id,
            same_private_scope(ConversationRun, conversation),
            ConversationRun.status.in_(RUN_TERMINAL_STATUSES),
        )
    )
    if run is None or not (run.metadata_json or {}).get(ATTEMPT_KEY):
        return
    try:
        context = await resolve_conversation_actor(db, conversation_id, conversation.user_id)
        agent_id = await require_queue_executable(db, conversation_id, context)
        denial = await queue_dispatch_denial(context, agent_id)
        if denial is not None:
            raise denial
    except (AppError, HTTPException) as exc:
        if block_reason(exc) in TRANSIENT or getattr(exc, "status", 0) >= 500:
            raise
        # The gateway could not authorize the conversation. Only serialize the stop.
        await db.scalar(
            select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()
        )
        result = (
            await db.execute(
                select(ConversationGoal, GoalAttempt)
                .join(GoalAttempt, GoalAttempt.goal_id == ConversationGoal.id)
                .where(
                    GoalAttempt.run_id == run.id,
                    GoalAttempt.status == "pending",
                    ConversationGoal.conversation_id == conversation_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if result:
            goal, attempt = result
            attempt.status = "blocked"
            if goal.version == attempt.goal_version and goal.status == "active":
                await block_goal(db, goal, "permission_revoked", attempt=attempt)
        return
    evaluated = await evaluate_goal_attempt(db, conversation=conversation, run=run, context=context)
    if evaluated is not None:
        goal, attempt = evaluated
        if attempt.status == "followup_reserved":
            await enqueue_reserved_followup(db, conversation, goal, attempt)


async def reconcile_committed_goal(
    run_id: UUID, conversation_id: UUID, scope: SessionTenant | OrgContext
) -> None:
    from app.services.conversation_run_queue_launch import facade_attr

    async with facade_attr("_company_session")(scope) as db:
        await reconcile_goal_run(db, run_id, conversation_id)
        await db.commit()


async def recover_goal_attempts() -> None:
    """Rediscover terminal commits whose worker died before recording the goal decision."""
    if not settings.conversation_goal_org_ids:
        return
    from app.services.conversation_run_queue_launch import facade_attr

    async with facade_attr("_session_factory")()() as db:
        rows = (
            await db.execute(
                select(
                    ConversationRun.id,
                    Conversation.id,
                    Conversation.tenant_id,
                    Conversation.user_id,
                )
                .select_from(GoalAttempt)
                .join(ConversationRun, ConversationRun.id == GoalAttempt.run_id)
                .join(Conversation, Conversation.id == ConversationRun.conversation_id)
                .where(
                    ConversationRun.status.in_(RUN_TERMINAL_STATUSES),
                    Conversation.org_id.in_(settings.conversation_goal_org_ids),
                    or_(
                        GoalAttempt.status == "pending",
                        (GoalAttempt.status == "followup_reserved")
                        & GoalAttempt.followup_input_id.is_(None),
                    ),
                )
                .order_by(ConversationRun.completed_at, ConversationRun.id)
                .limit(100)
            )
        ).all()
    for run_id, conversation_id, tenant_id, user_id in rows:
        try:
            await reconcile_committed_goal(
                run_id, conversation_id, SessionTenant(tenant_id, user_id)
            )
        except Exception:
            logger.exception("goal reconciliation will be retried run_id=%s", run_id)


async def allow_automatic_start(
    db: AsyncSession, *, conversation: Conversation, input_id: UUID, context: OrgContext
) -> bool:
    if not await verify_goal_followup(db, conversation=conversation, input_id=input_id):
        return False
    from app.services.conversation_goal_interrupts import waiting_goal_input_reason

    if reason := await waiting_goal_input_reason(
        db, conversation_id=conversation.id, context=context
    ):
        await block_goal_followup(db, conversation=conversation, input_id=input_id, reason=reason)
        return False
    if conversation.queue_paused:
        await block_goal_followup(
            db, conversation=conversation, input_id=input_id, reason="queue_paused"
        )
        return False
    try:
        agent_id = await require_queue_executable(db, conversation.id, context)
        policy = (
            await db.execute(
                select(Agent.runtime_profile, Agent.middleware_configs).where(Agent.id == agent_id)
            )
        ).one_or_none()
        if policy is None or not goal_setup_allowed(
            policy.runtime_profile, policy.middleware_configs or []
        ):
            await block_goal_followup(
                db, conversation=conversation, input_id=input_id, reason="policy_blocked"
            )
            return False
        denial = await queue_dispatch_denial(context, agent_id)
        if denial is not None:
            raise denial
        await refuse_run(db, tenant_id=context.tenant_id, agent_id=agent_id)
        await UsageLimits.check(
            db, Subject(context.tenant_id, context.org_id, context.user_id, agent_id)
        )
    except (HTTPException, AppError) as exc:
        reason = "usage_limit" if getattr(exc, "status", None) == 429 else "permission_revoked"
        await block_goal_followup(db, conversation=conversation, input_id=input_id, reason=reason)
        return False
    return True


async def automatic_run_can_start(
    db: AsyncSession, run: ConversationRun, context: OrgContext
) -> bool:
    """Final admission under the same locks and transaction as queued -> running."""
    key = (run.metadata_json or {}).get(INPUT_KEY)
    if key is None:
        return True
    conversation = await db.get(Conversation, run.conversation_id)
    if conversation is None:
        return False
    allowed = await allow_automatic_start(
        db, conversation=conversation, input_id=UUID(key), context=context
    )
    if allowed:
        return True
    from app.services.conversation_run_service import request_cancel_run

    await request_cancel_run(db, run, reason="goal_stopped")
    await db.commit()
    return False


async def stop_reservations_on_denial(
    db: AsyncSession, conversation_id: UUID, scope: SessionTenant | OrgContext, error: Exception
) -> None:
    """A worker may record an execution stop even when its owner lost membership."""
    status = error.status if isinstance(error, AppError) else getattr(error, "status_code", None)
    if status not in {403, 404, 409} or block_reason(error) in TRANSIENT:
        return
    conversation = await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            goal_reservation_owner_predicate(scope),
        )
        .with_for_update()
    )
    if conversation is None or (
        isinstance(scope, OrgContext) and scope.org_id != conversation.org_id
    ):
        return
    rows = (
        await db.execute(
            select(ConversationGoal, GoalAttempt, ConversationRunInput)
            .join(GoalAttempt, GoalAttempt.goal_id == ConversationGoal.id)
            .join(ConversationRunInput, ConversationRunInput.id == GoalAttempt.followup_input_id)
            .where(
                ConversationGoal.conversation_id == conversation.id,
                same_private_scope(ConversationGoal, conversation),
                ConversationGoal.status == "active",
                GoalAttempt.status == "followup_reserved",
                GoalAttempt.goal_version == ConversationGoal.version,
                ConversationRunInput.conversation_id == conversation.id,
                owned_input_predicate(conversation),
                ConversationRunInput.source == GOAL_INPUT_SOURCE,
                ConversationRunInput.status.in_(("pending", "claimed")),
            )
            .with_for_update()
        )
    ).all()
    for goal, attempt, item in rows:
        await block_goal(db, goal, "permission_revoked", attempt=attempt)
        if item.status == "pending":
            item.status = "canceled"
            item.revision += 1
    if rows:
        await db.commit()
