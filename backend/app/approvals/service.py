"""Approval requests: record, decide once, expire (feature plan v2 순번 13, 17.2).

A run that stops for a person's approval (a HITL interrupt with ``action_requests``) gets
one ``ApprovalRequest`` row per interrupt (``record_pending``). Whoever decides first, in
the chat's approval card or in the approval inbox, moves the row away from ``pending``
with a compare-and-set (``claim``) inside the transaction that queues the resumed run;
everyone after that gets ``ApprovalStale``. A row past ``expires_at`` can no longer be
approved; ``expire`` ends it as ``expired`` (the inbox module then resumes the run with a
rejection so the agent finishes its turn).

``ask_user`` questions and connection prompts are not approvals and get no row.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.authz import registration
from app.authz.audit_actions import (
    APPROVAL_DECIDED,
    APPROVAL_EXPIRED,
    APPROVAL_REQUESTED,
)
from app.authz.org_context import OrgContext
from app.config import settings
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import ConversationRun
from app.services import audit_service

# Run principal source (``ConversationRun.run_source``) → approval source (17.3 rows).
_SOURCES: Final = {
    "CHAT": "chat",
    "TRIGGER": "trigger",
    "API": "api",
    "SUBAGENT": "subagent",
    "WORKFLOW": "workflow",
}
_NOT_APPROVALS: Final = frozenset({"ask_user"})
_REASONS: Final = {"expiry": "approval_expired", "emergency_stop": "emergency_stop"}


class ApprovalStale(Exception):
    """The request was already decided, expired or no longer matches the run."""

    def __init__(self, status: str) -> None:
        super().__init__(f"approval request is {status}")
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def digest(value: Any) -> str:
    """A stable hash of tool arguments (the decider never sees more than this of them)."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def approval_actions(payload: Any) -> list[Mapping[str, Any]] | None:
    """The approval actions of an interrupt payload; ``None`` for questions and prompts."""
    if not isinstance(payload, Mapping):
        return None
    requests = payload.get("action_requests")
    if not isinstance(requests, list) or not requests:
        return None
    actions = [item for item in requests if isinstance(item, Mapping)]
    if len(actions) != len(requests):
        return None
    if all(action.get("name") in _NOT_APPROVALS for action in actions):
        return None
    return actions


def _allowed(payload: Mapping[str, Any], index: int, name: Any) -> list[str]:
    configs = payload.get("review_configs")
    if not isinstance(configs, list):
        return []
    picked: Any = configs[index] if index < len(configs) else None
    if not isinstance(picked, Mapping) or picked.get("action_name") not in (None, name):
        picked = next(
            (c for c in configs if isinstance(c, Mapping) and c.get("action_name") == name), None
        )
    decisions = picked.get("allowed_decisions") if isinstance(picked, Mapping) else None
    return [str(d) for d in decisions] if isinstance(decisions, list) else []


def _view(payload: Mapping[str, Any], secret_values: Iterable[str]) -> list[dict[str, Any]]:
    """The actions as the decider sees them: names, masked arguments, allowed decisions."""
    actions = approval_actions(payload) or []
    masked = redact_protocol_data(
        "input",
        [dict(action) for action in actions],
        redact_memory=False,
        secret_values=list(secret_values),
    )
    return [
        {
            "name": str(action.get("name") or ""),
            "args": shown.get("args") if isinstance(shown, Mapping) else None,
            "description": str(action.get("description") or "")[:2000] or None,
            "allowed_decisions": _allowed(payload, index, action.get("name")),
        }
        for index, (action, shown) in enumerate(zip(actions, masked, strict=True))
    ]


def args_digest(payload: Mapping[str, Any]) -> str:
    actions = approval_actions(payload) or []
    return digest([[a.get("name"), a.get("args")] for a in actions])


@dataclass(frozen=True, slots=True)
class PendingInterrupt:
    interrupt_id: str
    payload: Mapping[str, Any]
    namespace: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class RunOrigin:
    """What the run is for: where it came from and which schedule or API run waits on it."""

    agent_name: str = ""
    source: str | None = None
    trigger_id: uuid.UUID | None = None
    trigger_run_id: uuid.UUID | None = None
    api_run_id: uuid.UUID | None = None
    # A workflow agent step that waits on the request (순번 23 W2c).
    workflow_run_id: uuid.UUID | None = None
    workflow_step_run_id: uuid.UUID | None = None


async def _inherited(db: AsyncSession, run: ConversationRun) -> ApprovalRequest | None:
    """The approval this run resumed: a later request in the chain keeps its origin."""
    return (
        await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.resumed_run_id == run.id).limit(1)
        )
    ).scalar_one_or_none()


async def record_pending(
    db: AsyncSession,
    run: ConversationRun,
    interrupts: Iterable[PendingInterrupt],
    *,
    origin: RunOrigin,
    secret_values: Iterable[str] = (),
) -> list[ApprovalRequest]:
    """One pending row per approval interrupt of ``run`` (idempotent per interrupt)."""
    if run.tenant_id is None:
        return []
    secrets = list(secret_values)
    parent = await _inherited(db, run) if run.parent_run_id is not None else None
    source = (
        (parent.source if parent is not None else None)
        or origin.source
        or _SOURCES.get(run.run_source or "", "chat")
    )
    trigger_id = origin.trigger_id or (parent.trigger_id if parent else None)
    trigger_run_id = origin.trigger_run_id or (parent.trigger_run_id if parent else None)
    api_run_id = origin.api_run_id or (parent.api_run_id if parent else None)
    workflow_run_id = origin.workflow_run_id or (parent.workflow_run_id if parent else None)
    workflow_step_run_id = origin.workflow_step_run_id or (
        parent.workflow_step_run_id if parent else None
    )
    created: list[ApprovalRequest] = []
    for interrupt in interrupts:
        if approval_actions(interrupt.payload) is None:
            continue
        exists = await db.scalar(
            select(ApprovalRequest.id).where(
                ApprovalRequest.run_id == run.id,
                ApprovalRequest.interrupt_id == interrupt.interrupt_id,
            )
        )
        if exists is not None:
            continue
        hashed = args_digest(interrupt.payload)
        earlier = await db.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(
                ApprovalRequest.conversation_id == run.conversation_id,
                ApprovalRequest.args_digest == hashed,
            )
        )
        row = ApprovalRequest(
            tenant_id=run.tenant_id,
            org_id=run.org_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            interrupt_id=interrupt.interrupt_id,
            namespace=list(interrupt.namespace),
            agent_id=run.agent_id,
            agent_name=origin.agent_name[:200],
            source=source,
            trigger_id=trigger_id,
            trigger_run_id=trigger_run_id,
            api_run_id=api_run_id,
            workflow_run_id=workflow_run_id,
            workflow_step_run_id=workflow_step_run_id,
            subject_user_id=run.user_id,
            requester_user_id=run.on_behalf_of_user_id or run.user_id,
            decider_user_id=run.user_id,
            actions=_view(interrupt.payload, secrets),
            args_digest=hashed,
            attempt=int(earlier or 0) + 1,
            policy_version=run.runtime_policy_version,
            policy_hash=run.runtime_policy_hash,
            status="pending",
            decision_no=0,
            expires_at=now() + timedelta(minutes=settings.approval_ttl_minutes),
        )
        db.add(row)
        created.append(row)
    if not created:
        return []
    await db.flush()
    for row in created:
        await _audit(db, row, APPROVAL_REQUESTED, outcome="success", actor_type="system")
    return created


def _status_for(decisions: Sequence[str]) -> str:
    if decisions and all(decision == "reject" for decision in decisions):
        return "rejected"
    if any(decision == "edit" for decision in decisions):
        return "edited"
    return "approved"


@dataclass(frozen=True, slots=True)
class Claim:
    """One decision: which interrupts of which run, by whom, through which channel."""

    run_id: uuid.UUID
    decisions: Mapping[str, Sequence[str]]  # interrupt id → decision types
    channel: str
    actor_id: uuid.UUID | None
    resumed_run_id: uuid.UUID | None = None
    edited_digests: Mapping[str, str] | None = None
    reason: str | None = None


async def claim(db: AsyncSession, request: Claim) -> list[ApprovalRequest]:
    """Move each named pending row to its decision exactly once (compare-and-set).

    Raises ``ApprovalStale`` when a row was already decided or has expired; the caller's
    transaction (which also holds the resumed run) must then roll back. Interrupts with no
    row (questions, runs from before this table) pass through.
    """
    rows = list(
        (
            await db.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == request.run_id,
                    ApprovalRequest.interrupt_id.in_(list(request.decisions)),
                )
            )
        ).scalars()
    )
    stamp = now()
    expiry = request.channel in {"expiry", "emergency_stop"}
    for row in rows:
        decisions = list(request.decisions[row.interrupt_id])
        status = (
            "expired"
            if request.channel == "expiry"
            else "canceled"
            if request.channel == "emergency_stop"
            else _status_for(decisions)
        )
        guard = [ApprovalRequest.id == row.id, ApprovalRequest.status == "pending"]
        if not expiry:
            guard.append(ApprovalRequest.expires_at > stamp)
        result = await db.execute(
            update(ApprovalRequest)
            .where(*guard)
            .values(
                status=status,
                channel=request.channel,
                decided_by=request.actor_id,
                decided_at=stamp,
                decisions=decisions,
                decision_no=ApprovalRequest.decision_no + 1,
                resumed_run_id=request.resumed_run_id,
                edited_args_digest=(request.edited_digests or {}).get(row.interrupt_id),
                reason=request.reason or _REASONS.get(request.channel),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            await db.refresh(row)
            current = row.status
            if current == "pending" and aware(row.expires_at) <= stamp:
                current = "expired"
            raise ApprovalStale(current)
        await db.refresh(row)
        action = APPROVAL_EXPIRED if status == "expired" else APPROVAL_DECIDED
        await _audit(
            db,
            row,
            action,
            outcome="denied" if status in {"rejected", "expired", "canceled"} else "success",
            actor_type="user" if request.actor_id else "system",
        )
    return rows


async def audit_requested(db: AsyncSession, row: ApprovalRequest) -> None:
    """The request's creation audit, for rows recorded outside ``record_pending``."""
    await _audit(db, row, APPROVAL_REQUESTED, outcome="success", actor_type="system")


async def claim_business(
    db: AsyncSession,
    row: ApprovalRequest,
    *,
    decision: str,
    channel: str,
    actor_id: uuid.UUID | None,
) -> ApprovalRequest:
    """``claim`` for a workflow approval node (no run to resume): one compare-and-set."""
    stamp = now()
    expiry = channel == "expiry"
    status = "expired" if expiry else _status_for([decision])
    guard = [ApprovalRequest.id == row.id, ApprovalRequest.status == "pending"]
    if not expiry:
        guard.append(ApprovalRequest.expires_at > stamp)
    result = await db.execute(
        update(ApprovalRequest)
        .where(*guard)
        .values(
            status=status,
            channel=channel,
            decided_by=actor_id,
            decided_at=stamp,
            decisions=[decision],
            decision_no=ApprovalRequest.decision_no + 1,
            reason=_REASONS.get(channel),
        )
        .execution_options(synchronize_session=False)
    )
    await db.refresh(row)
    if result.rowcount != 1:  # type: ignore[attr-defined]
        current = row.status
        if current == "pending" and aware(row.expires_at) <= stamp:
            current = "expired"
        raise ApprovalStale(current)
    action = APPROVAL_EXPIRED if status == "expired" else APPROVAL_DECIDED
    await _audit(
        db,
        row,
        action,
        outcome="denied" if status in {"rejected", "expired"} else "success",
        actor_type="user" if actor_id else "system",
    )
    return row


async def due_for_expiry(db: AsyncSession, *, limit: int = 100) -> list[ApprovalRequest]:
    return list(
        (
            await db.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.status == "pending", ApprovalRequest.expires_at <= now())
                .order_by(ApprovalRequest.expires_at)
                .limit(limit)
            )
        ).scalars()
    )


async def due_for_notice(db: AsyncSession, *, limit: int = 100) -> list[ApprovalRequest]:
    soon = now() + timedelta(minutes=settings.approval_expiring_notice_minutes)
    return list(
        (
            await db.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.status == "pending",
                    ApprovalRequest.expires_at <= soon,
                    ApprovalRequest.expires_at > now(),
                    ApprovalRequest.expiring_notified_at.is_(None),
                )
                .order_by(ApprovalRequest.expires_at)
                .limit(limit)
            )
        ).scalars()
    )


async def pending_for_run(db: AsyncSession, run_id: uuid.UUID) -> list[ApprovalRequest]:
    return list(
        (
            await db.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == run_id, ApprovalRequest.status == "pending"
                )
            )
        ).scalars()
    )


async def _mine(db: AsyncSession, context: OrgContext) -> ColumnElement[bool]:
    """The caller's requests to decide in the current organization (or the company's).

    A registration request has no one decider (tool-governance-plan G9): company
    administrators see the company's, an organization's administrators see theirs.
    """
    own = and_(
        ApprovalRequest.kind != "registration",
        ApprovalRequest.decider_user_id == context.user_id,
        or_(ApprovalRequest.org_id == context.org_id, ApprovalRequest.org_id.is_(None)),
    )
    scope = await registration.decider_scope(db, context)
    if scope is None:
        return own
    registrations = and_(
        ApprovalRequest.kind == "registration", ApprovalRequest.tenant_id == context.tenant_id
    )
    if scope == "organization":
        registrations = and_(registrations, ApprovalRequest.org_id == context.org_id)
    return or_(own, registrations)


async def list_for_decider(
    db: AsyncSession, context: OrgContext, *, pending: bool, limit: int
) -> list[ApprovalRequest]:
    query = select(ApprovalRequest).where(await _mine(db, context))
    if pending:
        query = query.where(ApprovalRequest.status == "pending").order_by(
            ApprovalRequest.expires_at
        )
    else:
        query = query.where(ApprovalRequest.status != "pending").order_by(
            ApprovalRequest.decided_at.desc().nulls_last(), ApprovalRequest.created_at.desc()
        )
    return list((await db.execute(query.limit(limit))).scalars())


async def pending_count(db: AsyncSession, context: OrgContext) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(ApprovalRequest)
        .where(await _mine(db, context), ApprovalRequest.status == "pending")
    )
    return int(count or 0)


async def _audit(
    db: AsyncSession, row: ApprovalRequest, action: str, *, outcome: str, actor_type: str
) -> None:
    await audit_service.record_event(
        db,
        actor_type=actor_type,
        actor_user_id=row.decided_by if action != APPROVAL_REQUESTED else None,
        action=action,
        target_type="approval_request",
        target_id=row.id,
        target_name_snapshot=", ".join(a.get("name", "") for a in row.actions)[:200] or None,
        target_owner_user_id=row.subject_user_id,
        owner_user_id=row.subject_user_id,
        outcome=outcome,
        reason_code=row.reason if action != APPROVAL_REQUESTED else None,
        run_id=row.run_id,
        tenant_id=row.tenant_id,
        org_id=row.org_id,
        metadata={
            "source": row.source,
            "status": row.status,
            "channel": row.channel,
            "decision_no": row.decision_no,
            "attempt": row.attempt,
            "interrupt_id": row.interrupt_id,
            "args_digest": row.args_digest,
            "decider_user_id": str(row.decider_user_id),
            "decided_by": str(row.decided_by) if row.decided_by else None,
            "decisions": list(row.decisions or []),
        },
    )


__all__ = [
    "ApprovalStale",
    "Claim",
    "PendingInterrupt",
    "RunOrigin",
    "approval_actions",
    "args_digest",
    "claim",
    "digest",
    "due_for_expiry",
    "due_for_notice",
    "list_for_decider",
    "pending_count",
    "pending_for_run",
    "record_pending",
]
