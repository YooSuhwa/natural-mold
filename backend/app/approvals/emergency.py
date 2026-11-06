"""Emergency stop (feature plan v2 순번 13, chapter 17.4).

One action stops an agent, every agent of one person, or one MCP server:

1. The stop row is written first; from then on nothing new runs under it: no run is
   created for a stopped agent (``refuse_run``) and no call reaches a stopped MCP server
   (``server_stopped``).
2. In the same transaction: the running runs are asked to cancel, the active schedules
   pause, the Agent API deployments are disabled and the pending approvals are canceled.
3. The view shows the state step by step: block received, new calls blocked, how many
   runs confirmed their cancellation, and that external side effects already made are
   unknown.

Who: the owner (of the agent, the server; a person for their own agents), an
organization admin of the target's organization, a company admin. A release needs a
reason and the owner or a company admin; it re-enables the deployments the stop disabled
and leaves the schedules paused for their owners to resume. Both are audited and the
owners are told.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.approval_scope import agents_of_person
from app.authz.audit_actions import EMERGENCY_STOP_APPLY, EMERGENCY_STOP_RELEASE
from app.authz.notifications import Target, notify
from app.authz.org_context import OrgContext
from app.authz.org_permissions import organization_permissions
from app.database import SessionTenant, async_session, tenant_session
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_api import AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import RUN_ACTIVE_STATUSES, ConversationRun
from app.models.emergency_stop import EmergencyStop
from app.models.mcp_server import McpServer
from app.models.organization import TenantMember
from app.models.user import User
from app.services import audit_service
from app.tenancy.context import tenant_roles

CANCEL_REASON: Final = "emergency_stop"
STOPPED_CODE: Final = "agent_emergency_stopped"


class StopError(AppError):
    pass


def _not_found() -> StopError:
    return StopError("emergency_stop_target_not_found", "대상을 찾을 수 없습니다", status=404)


def stopped_error() -> AppError:
    return AppError(STOPPED_CODE, "긴급 중지된 에이전트는 실행할 수 없습니다", status=409)


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _Target:
    name: str
    owner_id: uuid.UUID | None
    org_id: uuid.UUID | None
    agent_ids: tuple[uuid.UUID, ...]
    owners: tuple[uuid.UUID, ...]


async def _is_company_admin(db: AsyncSession, context: OrgContext) -> bool:
    return "admin" in await tenant_roles(db, context.tenant_id, context.user_id)


async def _is_org_admin(db: AsyncSession, context: OrgContext, org_id: uuid.UUID | None) -> bool:
    if org_id is None or org_id != context.org_id:
        return False
    roles, _ = await organization_permissions(db, org_id, context.user_id)
    return "admin" in roles


async def _target(
    db: AsyncSession, context: OrgContext, scope_type: str, scope_id: uuid.UUID
) -> _Target:
    if scope_type == "agent":
        agent = await db.get(Agent, scope_id)
        if agent is None or agent.tenant_id != context.tenant_id:
            raise _not_found()
        return _Target(agent.name, agent.user_id, agent.org_id, (agent.id,), (agent.user_id,))
    if scope_type == "user":
        member = await db.get(TenantMember, (context.tenant_id, scope_id))
        user = await db.get(User, scope_id)
        if member is None or user is None:
            raise _not_found()
        agents = tuple(
            await db.scalars(select(Agent.id).where(agents_of_person(context.tenant_id, scope_id)))
        )
        return _Target(user.display_name or user.name, scope_id, None, agents, (scope_id,))
    if scope_type == "mcp_server":
        server = await db.get(McpServer, scope_id)
        if server is None or server.tenant_id != context.tenant_id:
            raise _not_found()
        owners = (server.user_id,) if server.user_id else ()
        return _Target(server.name, server.user_id, server.org_id, (), owners)
    raise StopError("invalid_scope", "알 수 없는 중지 범위입니다", status=422)


async def _may_stop(db: AsyncSession, context: OrgContext, target: _Target) -> bool:
    return (
        target.owner_id == context.user_id
        or await _is_org_admin(db, context, target.org_id)
        or await _is_company_admin(db, context)
    )


async def _may_release(db: AsyncSession, context: OrgContext, row: EmergencyStop) -> bool:
    owner = (row.effects or {}).get("owner_id")
    return owner == str(context.user_id) or await _is_company_admin(db, context)


async def _cancel_runs(db: AsyncSession, agent_ids: Sequence[uuid.UUID]) -> list[str]:
    from app.services.conversation_run_service import request_cancel_run

    if not agent_ids:
        return []
    runs = list(
        await db.scalars(
            select(ConversationRun)
            .where(
                ConversationRun.agent_id.in_(agent_ids),
                ConversationRun.status.in_(RUN_ACTIVE_STATUSES),
            )
            .with_for_update()
        )
    )
    for run in runs:
        await request_cancel_run(db, run, reason=CANCEL_REASON)
    return [str(run.id) for run in runs]


async def _pause_schedules(db: AsyncSession, agent_ids: Sequence[uuid.UUID]) -> list[str]:
    if not agent_ids:
        return []
    triggers = list(
        await db.scalars(
            select(AgentTrigger).where(
                AgentTrigger.agent_id.in_(agent_ids), AgentTrigger.status == "active"
            )
        )
    )
    for trigger in triggers:
        trigger.status = "paused"
        trigger.next_run_at = None
    return [str(trigger.id) for trigger in triggers]


async def _disable_deployments(db: AsyncSession, agent_ids: Sequence[uuid.UUID]) -> list[str]:
    if not agent_ids:
        return []
    deployments = list(
        await db.scalars(
            select(AgentDeployment).where(
                AgentDeployment.agent_id.in_(agent_ids), AgentDeployment.status == "active"
            )
        )
    )
    for deployment in deployments:
        deployment.status = "disabled"
    return [str(deployment.id) for deployment in deployments]


async def _cancel_approvals(db: AsyncSession, agent_ids: Sequence[uuid.UUID]) -> int:
    if not agent_ids:
        return 0
    result = await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.agent_id.in_(agent_ids), ApprovalRequest.status == "pending")
        .values(
            status="canceled",
            channel="emergency_stop",
            reason="emergency_stop",
            decided_at=now(),
            decision_no=ApprovalRequest.decision_no + 1,
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _sync_schedules(db: AsyncSession, trigger_ids: Sequence[str]) -> None:
    from app.services import trigger_service

    for trigger_id in trigger_ids:
        trigger = await db.get(AgentTrigger, uuid.UUID(trigger_id))
        if trigger is not None:
            await trigger_service.sync_scheduler_for_trigger(db, trigger)


def _target_ref(row: EmergencyStop) -> Target:
    return Target(row.scope_type, row.scope_id, row.scope_name)


async def _tell_owners(
    db: AsyncSession, row: EmergencyStop, kind: str, actor_id: uuid.UUID | None
) -> None:
    owners = [uuid.UUID(owner) for owner in (row.effects or {}).get("owners", [])]
    await notify(
        db,
        kind=kind,
        recipients=owners,
        tenant_id=row.tenant_id,
        org_id=row.org_id,
        target=_target_ref(row),
        actor_id=actor_id,
        payload={"scope_type": row.scope_type, "reason": (row.release_reason or row.reason)[:200]},
        link="/approvals?tab=stops",
    )


async def _audit(db: AsyncSession, row: EmergencyStop, action: str, context: OrgContext) -> None:
    effects = row.effects or {}
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=context.user_id,
        action=action,
        target_type=row.scope_type,
        target_id=row.scope_id,
        target_name_snapshot=row.scope_name[:200] or None,
        target_owner_user_id=uuid.UUID(effects["owner_id"]) if effects.get("owner_id") else None,
        outcome="success",
        reason_message=(row.release_reason if action == EMERGENCY_STOP_RELEASE else row.reason),
        tenant_id=row.tenant_id,
        org_id=row.org_id,
        metadata={
            "stop_id": str(row.id),
            "runs_cancel_requested": len(effects.get("run_ids", [])),
            "schedules_paused": len(effects.get("trigger_ids", [])),
            "deployments_disabled": len(effects.get("deployment_ids", [])),
            "approvals_canceled": effects.get("approvals_canceled", 0),
            "agents": len(effects.get("agent_ids", [])),
        },
    )


async def stop(
    db: AsyncSession,
    context: OrgContext,
    *,
    scope_type: str,
    scope_id: uuid.UUID,
    reason: str,
) -> EmergencyStop:
    """Apply a stop (idempotent per target: an active stop is returned as it is)."""
    target = await _target(db, context, scope_type, scope_id)
    if not await _may_stop(db, context, target):
        raise _not_found()
    existing = await db.scalar(
        select(EmergencyStop).where(
            EmergencyStop.tenant_id == context.tenant_id,
            EmergencyStop.scope_type == scope_type,
            EmergencyStop.scope_id == scope_id,
            EmergencyStop.status == "active",
        )
    )
    if existing is not None:
        return existing
    row = EmergencyStop(
        tenant_id=context.tenant_id,
        org_id=target.org_id,
        scope_type=scope_type,
        scope_id=scope_id,
        scope_name=target.name[:200],
        status="active",
        reason=reason[:500],
        stopped_by=context.user_id,
        stopped_at=now(),
        effects={},
    )
    db.add(row)
    await db.flush()  # the block itself, before anything else is touched
    agents = target.agent_ids
    trigger_ids = await _pause_schedules(db, agents)
    row.effects = {
        "owner_id": str(target.owner_id) if target.owner_id else None,
        "owners": [str(owner) for owner in target.owners],
        "agent_ids": [str(agent) for agent in agents],
        "run_ids": await _cancel_runs(db, agents),
        "trigger_ids": trigger_ids,
        "deployment_ids": await _disable_deployments(db, agents),
        "approvals_canceled": await _cancel_approvals(db, agents),
    }
    await db.flush()
    await _audit(db, row, EMERGENCY_STOP_APPLY, context)
    await _tell_owners(db, row, "emergency_stop.applied", context.user_id)
    await db.commit()
    _cancel_local_tasks(row.effects["run_ids"])
    await _sync_schedules(db, trigger_ids)
    await db.refresh(row)
    return row


def _cancel_local_tasks(run_ids: Sequence[str]) -> None:
    """Runs owned by this process stop now; others see ``canceling`` on their next poll."""
    from app.services.conversation_run_worker import get_run_task_registry

    registry = get_run_task_registry()
    for run_id in run_ids:
        registry.request_cancel(uuid.UUID(run_id), reason="stop")


async def release(
    db: AsyncSession, context: OrgContext, *, stop_id: uuid.UUID, reason: str
) -> EmergencyStop:
    row = await db.get(EmergencyStop, stop_id, with_for_update=True)
    if row is None or row.tenant_id != context.tenant_id:
        raise _not_found()
    if not await _may_release(db, context, row):
        raise StopError(
            "emergency_stop_release_forbidden",
            "해제는 소유자나 회사 관리자만 할 수 있습니다",
            status=403,
        )
    if row.status != "active":
        return row
    row.status = "released"
    row.released_by = context.user_id
    row.released_at = now()
    row.release_reason = reason[:500]
    deployments = [uuid.UUID(d) for d in (row.effects or {}).get("deployment_ids", [])]
    if deployments:
        await db.execute(
            update(AgentDeployment)
            .where(AgentDeployment.id.in_(deployments), AgentDeployment.status == "disabled")
            .values(status="active")
            .execution_options(synchronize_session=False)
        )
    await _audit(db, row, EMERGENCY_STOP_RELEASE, context)
    await _tell_owners(db, row, "emergency_stop.released", context.user_id)
    await db.commit()
    await db.refresh(row)
    return row


async def state(db: AsyncSession, row: EmergencyStop) -> dict[str, Any]:
    """The stop's progress (17.4 처리 상태), computed now."""
    effects = row.effects or {}
    run_ids = [uuid.UUID(r) for r in effects.get("run_ids", [])]
    still_running = 0
    if run_ids:
        still_running = len(
            list(
                await db.scalars(
                    select(ConversationRun.id).where(
                        ConversationRun.id.in_(run_ids),
                        ConversationRun.status.in_(RUN_ACTIVE_STATUSES),
                    )
                )
            )
        )
    return {
        "block_received": True,
        "new_calls_blocked": row.status == "active",
        "runs_cancel_requested": len(run_ids),
        "runs_cancel_confirmed": len(run_ids) - still_running,
        "schedules_paused": len(effects.get("trigger_ids", [])),
        "deployments_disabled": len(effects.get("deployment_ids", [])),
        "approvals_canceled": effects.get("approvals_canceled", 0),
        "external_side_effects": "unknown",
    }


async def visible_stops(
    db: AsyncSession, context: OrgContext, *, active_only: bool
) -> list[EmergencyStop]:
    """Every stop of the company for a company admin; otherwise the ones about me."""
    query = select(EmergencyStop).where(EmergencyStop.tenant_id == context.tenant_id)
    if active_only:
        query = query.where(EmergencyStop.status == "active")
    rows = list(await db.scalars(query.order_by(EmergencyStop.stopped_at.desc()).limit(200)))
    if await _is_company_admin(db, context):
        return rows
    me = str(context.user_id)
    return [
        row
        for row in rows
        if row.stopped_by == context.user_id or me in (row.effects or {}).get("owners", [])
    ]


# --- enforcement ----------------------------------------------------------------------


async def stop_for_agent(
    db: AsyncSession, *, tenant_id: uuid.UUID, agent_id: uuid.UUID
) -> EmergencyStop | None:
    owner = await db.scalar(select(Agent.user_id).where(Agent.id == agent_id))
    scopes = [(EmergencyStop.scope_type == "agent") & (EmergencyStop.scope_id == agent_id)]
    if owner is not None:
        scopes.append((EmergencyStop.scope_type == "user") & (EmergencyStop.scope_id == owner))
    return await db.scalar(
        select(EmergencyStop)
        .where(
            EmergencyStop.tenant_id == tenant_id,
            EmergencyStop.status == "active",
            or_(*scopes),
        )
        .limit(1)
    )


async def refuse_run(db: AsyncSession, *, tenant_id: uuid.UUID | None, agent_id: uuid.UUID) -> None:
    """No run starts for a stopped agent (or an agent of a stopped person)."""
    if tenant_id is not None and await stop_for_agent(db, tenant_id=tenant_id, agent_id=agent_id):
        raise stopped_error()


async def server_stopped(tenant_id: uuid.UUID, server_id: uuid.UUID) -> bool:
    """A stopped MCP server takes no call (checked in its own session, per call)."""
    async with tenant_session(SessionTenant(tenant_id), maker=async_session) as db:
        found = await db.scalar(
            select(EmergencyStop.id).where(
                EmergencyStop.tenant_id == tenant_id,
                EmergencyStop.status == "active",
                EmergencyStop.scope_type == "mcp_server",
                EmergencyStop.scope_id == server_id,
            )
        )
    return found is not None


__all__ = [
    "CANCEL_REASON",
    "EmergencyStop",
    "STOPPED_CODE",
    "StopError",
    "refuse_run",
    "release",
    "server_stopped",
    "state",
    "stop",
    "stop_for_agent",
    "stopped_error",
    "visible_stops",
]
