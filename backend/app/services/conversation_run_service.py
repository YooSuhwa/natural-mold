"""Conversation run service compatibility facade (Task 10 I4 split).

``create_run`` and the run state machine live in ``conversation_run_creation``;
terminal-output and stale-sweep persistence live in ``conversation_run_creation_persistence``.
Both are re-exported here unchanged; read queries stay here.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.agent_runtime import event_names
from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    lock_run_conversation,
    private_run_predicate,
    resume_parent_predicate,
)
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.config import settings
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.models.conversation_run import RUN_ACTIVE_STATUSES, RUN_TERMINAL_STATUSES, ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import trace_storage
from app.services.artifact_service import finalize_artifacts_for_run
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_run_creation import (
    ALLOWED_TRANSITIONS,
    RunSource,
    RunStatus,
    _conflict,
    _is_active_run_unique_violation,
    create_run,
    heartbeat_run,
    request_cancel_run,
    transition_run,
    utc_now_naive,
)
from app.services.conversation_run_creation_persistence import (
    _pause_uncertain_owned_run_queues,
    finalize_run_outputs_for_status,
    mark_stale_active_runs,
    sweep_stale_conversation_runs,
)
from app.services.conversation_run_envelope_projection import project_owned_envelopes
from app.services.conversation_runtime_policy import (
    copy_snapshot_to_run,
    ensure_conversation_runtime_policy,
    require_parent_snapshot_match,
)

logger = logging.getLogger(__name__)


async def _latest_interrupted_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ConversationRun | None:
    resume_child = aliased(ConversationRun)
    resume_child_exists = (
        select(resume_child.id)
        .where(
            resume_parent_predicate(resume_child),
            resume_child.source == "resume",
        )
        .exists()
    )
    result = await db.execute(
        select(ConversationRun)
        .where(
            ConversationRun.conversation_id == conversation_id,
            private_run_predicate(context),
            ConversationRun.status == "interrupted",
            ~resume_child_exists,
        )
        .order_by(ConversationRun.completed_at.desc(), ConversationRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_interrupted_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ConversationRun | None:
    await require_private_actor(db, context)
    return await _latest_interrupted_run(
        db,
        conversation_id=conversation_id,
        context=context,
    )


async def get_active_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ConversationRun | None:
    await require_private_actor(db, context)
    result = await db.execute(
        select(ConversationRun).where(
            ConversationRun.conversation_id == conversation_id,
            private_run_predicate(context),
            ConversationRun.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def active_runs_for_conversations(
    db: AsyncSession,
    conversation_ids: Sequence[uuid.UUID],
    context: OrgContext,
) -> dict[uuid.UUID, ConversationRun]:
    await require_private_actor(db, context)
    if not conversation_ids:
        return {}
    resume_child = aliased(ConversationRun)
    resume_child_exists = (
        select(resume_child.id)
        .where(
            resume_parent_predicate(resume_child),
            resume_child.source == "resume",
        )
        .exists()
    )
    result = await db.execute(
        select(ConversationRun)
        .where(
            ConversationRun.conversation_id.in_(conversation_ids),
            private_run_predicate(context),
            or_(
                ConversationRun.is_active.is_(True),
                ((ConversationRun.status == "interrupted") & (~resume_child_exists)),
            ),
        )
        .order_by(
            ConversationRun.conversation_id,
            ConversationRun.is_active.desc(),
            ConversationRun.created_at.desc(),
        )
    )
    runs: dict[uuid.UUID, ConversationRun] = {}
    for run in result.scalars().all():
        runs.setdefault(run.conversation_id, run)
    return runs


async def current_run_for_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ConversationRun | None:
    return (await active_runs_for_conversations(db, [conversation_id], context)).get(
        conversation_id
    )


async def latest_run_for_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ConversationRun | None:
    """최신 run을 상태와 무관하게 1건 반환.

    ``active_run`` 은 active/미해결 interrupted 만 보고하므로 canceled 처럼
    terminal 로 끝난 마지막 turn 의 상태를 알 수 없다. 메시지는 checkpointer
    파생이라 run_id(uuid4) ↔ message id(uuid5) 매칭이 불가능해, "마지막 turn
    이 취소되었다" 는 사실은 conversation 단위 최신 run 으로만 durable 하게
    전달할 수 있다 (``ix_conversation_runs_conversation_created`` 인덱스 사용).
    """
    await require_private_actor(db, context)
    result = await db.execute(
        select(ConversationRun)
        .where(ConversationRun.conversation_id == conversation_id, private_run_predicate(context))
        .order_by(ConversationRun.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def get_run_for_user(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    context: OrgContext,
    for_update: bool = False,
) -> ConversationRun | None:
    """``for_update=True`` 는 이후 ``transition_run`` 으로 상태를 바꿀 호출자용.

    Postgres 에서 row lock 으로 worker/heartbeat 와의 read-modify-write 경합을
    직렬화한다 (SQLite dialect 는 FOR UPDATE 를 무시 — 테스트는 단일 루프라 안전).
    """
    await require_private_actor(db, context)
    stmt = select(ConversationRun).where(
        ConversationRun.id == run_id,
        ConversationRun.conversation_id == conversation_id,
        private_run_predicate(context),
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


__all__ = [
    "ALLOWED_TRANSITIONS",
    "RUN_ACTIVE_STATUSES",
    "RUN_TERMINAL_STATUSES",
    "active_runs_for_conversations",
    "create_run",
    "current_run_for_conversation",
    "finalize_run_outputs_for_status",
    "get_active_run",
    "get_latest_interrupted_run",
    "get_run_for_user",
    "heartbeat_run",
    "latest_run_for_conversation",
    "mark_stale_active_runs",
    "request_cancel_run",
    "sweep_stale_conversation_runs",
    "transition_run",
]
# Compatibility re-exports: names this module exposed before the I4 split.
__all__ += ["HTTPException", "IntegrityError", "AsyncSession", "aliased", "event_names"]
__all__ += ["RuntimePolicySnapshotError", "RunConversationTarget", "lock_run_conversation"]
__all__ += ["private_run_predicate", "resume_parent_predicate", "OrgContext", "settings"]
__all__ += ["require_private_actor", "ConflictError", "Conversation", "ConversationRun"]
__all__ += ["ConversationRunInput", "ConversationPolicyTarget", "trace_storage", "func", "or_"]
__all__ += ["finalize_artifacts_for_run", "record_conversation_run_audit", "select"]
__all__ += ["_conflict", "_is_active_run_unique_violation", "RunSource", "RunStatus"]
__all__ += ["_pause_uncertain_owned_run_queues", "utc_now_naive", "project_owned_envelopes"]
__all__ += ["copy_snapshot_to_run", "ensure_conversation_runtime_policy"]
__all__ += ["require_parent_snapshot_match"]
