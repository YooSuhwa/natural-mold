"""Transfer an enrolled ordinary chat between workers under the execution gateway."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.conversation_scope import resolve_conversation_actor
from app.chat_recovery.identity import recovery_runtime_digest
from app.config import settings
from app.database import SessionTenant, tenant_session
from app.exceptions import AppError
from app.guardrails.pinning import guardrails_for_takeover
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, ConversationRunToolReceipt, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.models.user import User
from app.services.audit_service import record_event
from app.services.conversation_agent_context import resolve_agent_context
from app.services.conversation_run_creation_persistence import finalize_run_outputs_for_status
from app.services.conversation_run_fence import claim_run_owner
from app.services.conversation_run_prepared_denied_recovery import lock_orphan_for_terminal_status
from app.services.conversation_run_queue_worker_metadata import queue_current_user
from app.services.conversation_run_recovery_checkpoint import (
    RecoveryCheckpointBlocked,
    continue_recovery_stream,
    resolve_recovery_checkpoint,
)
from app.services.conversation_run_state import transition_run
from app.services.conversation_run_task_registry import RunTaskRegistry
from app.services.conversation_run_worker import get_run_task_registry, start_conversation_run

logger = logging.getLogger(__name__)
type SessionFactory = Callable[[], AsyncSession]


@dataclass(frozen=True, slots=True)
class _Candidate:
    id: uuid.UUID
    conversation_id: uuid.UUID
    agent_id: uuid.UUID | None
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    org_id: uuid.UUID
    generation: int


def _matches(run: ConversationRun | None, row: _Candidate, stale_before: datetime) -> bool:
    return bool(
        run is not None
        and run.is_active
        and run.status in ("running", "canceling")
        and run.owner_generation == row.generation
        and run.owner_token is not None
        and run.org_id == row.org_id
        and run.tenant_id == row.tenant_id
        and run.user_id == row.user_id
        and run.conversation_id == row.conversation_id
        and run.agent_id == row.agent_id
        and run.runtime_materialized_at is None
        and (run.heartbeat_at or run.started_at or run.created_at) <= stale_before
    )


async def _audit(db: AsyncSession, run: ConversationRun, *, reason: str | None = None) -> None:
    # Operators can correlate the receipt state and generation by run ID. No tool
    # arguments, output, credentials, user messages or bearer owner tokens enter audit.
    await record_event(
        db,
        action="conversation.run_recovery_blocked" if reason else "conversation.run_recovered",
        actor_type="system",
        owner_user_id=run.user_id,
        target_owner_user_id=run.user_id,
        target_type="conversation",
        target_id=run.conversation_id,
        tenant_id=run.tenant_id,
        org_id=run.org_id,
        outcome="skipped" if reason else "success",
        reason_code=reason,
        run_id=run.id,
        metadata={
            "owner_generation": run.owner_generation,
            "recovery_attempts": run.recovery_attempts,
            "external_effect_unconfirmed": reason == "recovery_effect_unconfirmed",
        },
    )


async def _stop(
    db: AsyncSession, run: ConversationRun, code: str, *, authorized: bool = True
) -> None:
    """Preserve partial output and hold pending work for a person's review."""
    parent = await db.get(Conversation, run.conversation_id)
    if parent is not None:
        parent.queue_paused = True
        parent.queue_paused_at = parent.queue_paused_at or utc_now_naive()
    queued = await db.scalar(
        select(ConversationRunInput)
        .where(ConversationRunInput.run_id == run.id, ConversationRunInput.status == "claimed")
        .with_for_update()
    )
    if queued is not None:
        queued.status = "failed"
        queued.revision += 1
    await transition_run(db, run, "failed", error_code=code, error_message=code)
    if authorized:
        await finalize_run_outputs_for_status(db, run, "failed")
    await _audit(db, run, reason=code)
    await db.commit()


async def _recover(
    row: _Candidate,
    *,
    stale_before: datetime,
    registry: RunTaskRegistry,
    session_factory: SessionFactory,
    company_session: Callable[[SessionTenant], AsyncSession],
) -> bool:
    scope = SessionTenant(tenant_id=row.tenant_id, user_id=row.user_id)
    # The platform sweep discovers identifiers. All private configuration is hydrated in
    # the original tenant, after current actor, agent and conversation authorization.
    async with company_session(scope) as db:
        try:
            context = await resolve_conversation_actor(db, row.conversation_id, row.user_id)
            if row.agent_id is None:
                run = None
            else:
                target = RunExecutionTarget(
                    row.id, RunConversationTarget(row.conversation_id, row.agent_id, context)
                )
                run = await lock_executable_run(db, target)
        except AppError:
            run = None
        if run is None:
            # The gateway refused authority. Release its locks before entering the narrow
            # terminal-only path. Recheck owner and heartbeat after reacquiring the row.
            await db.rollback()
            async with session_factory() as terminal_db:
                lost = await lock_orphan_for_terminal_status(terminal_db, row)
                if lost is not None and _matches(lost, row, stale_before):
                    await _stop(terminal_db, lost, "recovery_authority_revoked", authorized=False)
            return False
        if not _matches(run, row, stale_before):
            return False
        # Even when the checkpoint is missing or recovery was switched off, an
        # outstanding external send needs a confirmation prompt before a human retries.
        unconfirmed = await db.scalar(
            select(ConversationRunToolReceipt.tool_call_id)
            .where(
                ConversationRunToolReceipt.run_id == run.id,
                ConversationRunToolReceipt.status == "started",
                ConversationRunToolReceipt.effect_kind != "read",
            )
            .limit(1)
        )
        if unconfirmed is not None:
            await _stop(db, run, "recovery_effect_unconfirmed")
            return False
        if run.status == "canceling":
            await transition_run(
                db, run, "canceled", cancellation_ack_worker_id=run.worker_instance_id
            )
            await finalize_run_outputs_for_status(db, run, "canceled", append_terminal_event=True)
            await db.commit()
            return False
        if not settings.chat_run_recovery_enabled:
            await _stop(db, run, "recovery_disabled")
            return False
        if run.recovery_attempts >= settings.chat_run_recovery_max_attempts:
            await _stop(db, run, "recovery_limit_reached")
            return False
        owner = await db.get(User, row.user_id)
        if owner is None or not owner.is_active:
            await _stop(db, run, "recovery_authority_revoked")
            return False
        user = queue_current_user(owner)
        try:
            cfg = await resolve_agent_context(db, row.conversation_id, user, context=context)
        except (AppError, HTTPException, ValueError):
            await _stop(db, run, "recovery_authority_revoked", authorized=False)
            return False
        pinned = await guardrails_for_takeover(db, run, tenant_id=row.tenant_id)
        if pinned is None:
            await _stop(db, run, "recovery_policy_changed")
            return False
        cfg = replace(
            cfg, guardrail_rules=pinned.rules, guardrail_resolved=True, tool_risk=pinned.tool_risk
        )
        if (
            not run.recovery_runtime_digest
            or recovery_runtime_digest(cfg) != run.recovery_runtime_digest
            or not (run.metadata_json or {}).get("recovery_preparation_digest")
            or cfg.runtime_policy.policy_hash != run.runtime_policy_hash
        ):
            await _stop(db, run, "recovery_policy_changed")
            return False
        try:
            checkpoint_id = await resolve_recovery_checkpoint(db, run, cfg)
        except RecoveryCheckpointBlocked as exc:
            await _stop(db, run, exc.code)
            return False
        fence = claim_run_owner(run, registry.worker_instance_id)
        run.recovery_attempts += 1
        await _audit(db, run)
        await db.commit()

    # A crash after commit leaves a new heartbeat and bounded attempt, never two callable
    # owners. start_conversation_run and the receiving worker both recheck authorization.
    try:
        await start_conversation_run(
            run_id=row.id,
            conversation_id=row.conversation_id,
            cfg=replace(cfg, checkpoint_id=checkpoint_id),
            user=user,
            input_payload=None,
            moldy_source="recovery",
            executor_fn=continue_recovery_stream,
            registry=registry,
            recovery_fence=fence,
        )
    except Exception:
        logger.exception("could not launch recovered conversation run id=%s", row.id)
        async with company_session(scope) as db:
            current = await lock_orphan_for_terminal_status(db, row)
            if (
                current is not None
                and current.owner_token == fence.owner_token
                and current.status == "running"
            ):
                await _stop(db, current, "recovery_launch_failed", authorized=False)
        return False
    return True


async def recover_stale_chat_runs(
    *,
    session_factory: SessionFactory,
    stale_before: datetime,
    registry: RunTaskRegistry | None = None,
    company_session: Callable[[SessionTenant], AsyncSession] = tenant_session,
) -> int:
    registry = registry or get_run_task_registry()
    async with session_factory() as db:
        rows = await db.execute(
            select(
                ConversationRun.id,
                ConversationRun.conversation_id,
                ConversationRun.agent_id,
                ConversationRun.user_id,
                Conversation.tenant_id,
                Conversation.org_id,
                ConversationRun.owner_generation,
            )
            .join(Conversation, Conversation.id == ConversationRun.conversation_id)
            .where(
                ConversationRun.owner_generation > 0,
                ConversationRun.is_active.is_(True),
                ConversationRun.status.in_(("running", "canceling")),
                ConversationRun.runtime_materialized_at.is_(None),
                func.coalesce(
                    ConversationRun.heartbeat_at,
                    ConversationRun.started_at,
                    ConversationRun.created_at,
                )
                <= stale_before,
            )
            .order_by(ConversationRun.heartbeat_at, ConversationRun.id)
        )
        candidates = [_Candidate(*row) for row in rows]
    launched = 0
    for candidate in candidates:
        if candidate.id in registry.active_run_ids():
            continue
        try:
            launched += int(
                await _recover(
                    candidate,
                    stale_before=stale_before,
                    registry=registry,
                    session_factory=session_factory,
                    company_session=company_session,
                )
            )
        except Exception:
            logger.exception("stale chat recovery unavailable id=%s", candidate.id)
    return launched
