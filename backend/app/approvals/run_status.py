"""Run status (feature plan v2 순번 13, 17.2 "실행 현황").

The runs I started and the runs of my agents, grouped as running (queued, running,
canceling), waiting (stopped for an approval nobody decided yet) and failed (failed,
stale), newest first. Another person's run of my agent shows only that it exists: agent,
state, source and times, never a way into that person's conversation. The company-wide
view for admins is a separate screen (26.4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.approval_scope import runs_started_or_owned
from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import RUN_ACTIVE_STATUSES, ConversationRun

RunState = Literal["running", "waiting", "failed"]
_FAILED: Final = ("failed", "stale")


@dataclass(frozen=True, slots=True)
class RunStatusItem:
    run_id: uuid.UUID
    state: RunState
    status: str
    agent_id: uuid.UUID | None
    agent_name: str
    source: str | None
    mine: bool
    conversation_id: uuid.UUID | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


def _state_filter(state: RunState):  # noqa: ANN202 - SQL expression
    if state == "running":
        return ConversationRun.status.in_(RUN_ACTIVE_STATUSES)
    if state == "failed":
        return ConversationRun.status.in_(_FAILED)
    return (ConversationRun.status == "interrupted") & exists().where(
        ApprovalRequest.run_id == ConversationRun.id, ApprovalRequest.status == "pending"
    )


async def list_runs(
    db: AsyncSession, context: OrgContext, *, state: RunState, limit: int
) -> list[RunStatusItem]:
    rows = (
        await db.execute(
            select(ConversationRun, Agent.name, Agent.user_id)
            .outerjoin(Agent, Agent.id == ConversationRun.agent_id)
            .where(
                runs_started_or_owned(context),
                _state_filter(state),
            )
            .order_by(ConversationRun.created_at.desc())
            .limit(limit)
        )
    ).all()
    items: list[RunStatusItem] = []
    for run, agent_name, _owner in rows:
        mine = run.user_id == context.user_id
        items.append(
            RunStatusItem(
                run_id=run.id,
                state=state,
                status=run.status,
                agent_id=run.agent_id,
                agent_name=agent_name or "",
                source=run.run_source,
                mine=mine,
                conversation_id=run.conversation_id if mine else None,
                error_code=run.error_code,
                created_at=run.created_at,
                completed_at=run.completed_at,
            )
        )
    return items


__all__ = ["RunState", "RunStatusItem", "list_runs"]
