"""Read-side service for the daily spend aggregate tables.

The spend writer rolls every agent invocation into ``daily_spend_user``,
``daily_spend_agent``, ``daily_spend_model``. This service exposes the
aggregated time-series rows the dashboard renders. The shape mirrors what
the frontend expects: a flat list of dicts that the UI can pivot client-side
into a chart or a table.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_model import DailySpendModel
from app.models.daily_spend_user import DailySpendUser
from app.models.model import Model
from app.models.user import User

TargetKind = Literal["user", "agent", "model"]
GroupBy = Literal["date", "target"]

# Hard cap on the lookback window so a careless query can't scan the entire
# spend history. 365 days is enough for a year-over-year dashboard view.
_MAX_LOOKBACK_DAYS = 365


def _resolve_window(
    from_date: date_type | None,
    to_date: date_type | None,
) -> tuple[date_type, date_type]:
    """Default the window to ``today - 30`` → ``today`` and clamp."""

    today = date_type.today()
    if to_date is None:
        to_date = today
    if from_date is None:
        from_date = to_date - timedelta(days=30)
    if from_date > to_date:
        from_date, to_date = to_date, from_date
    earliest = to_date - timedelta(days=_MAX_LOOKBACK_DAYS)
    if from_date < earliest:
        from_date = earliest
    return from_date, to_date


def _table_for(target_kind: TargetKind) -> tuple[Any, Any]:
    """Return ``(table_class, target_id_column)`` for the requested axis."""

    if target_kind == "user":
        return DailySpendUser, DailySpendUser.user_id
    if target_kind == "agent":
        return DailySpendAgent, DailySpendAgent.agent_id
    if target_kind == "model":
        return DailySpendModel, DailySpendModel.model_id
    raise ValueError(f"unknown target_kind: {target_kind}")


async def _label_map(
    db: AsyncSession,
    target_kind: TargetKind,
    target_ids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Resolve human-readable labels for the given target ids."""

    if not target_ids:
        return {}
    if target_kind == "user":
        result = await db.execute(
            select(User.id, User.name, User.email).where(User.id.in_(target_ids))
        )
        return {row.id: (row.name or row.email or str(row.id)) for row in result.all()}
    if target_kind == "agent":
        result = await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(target_ids)))
        return {row.id: row.name for row in result.all()}
    if target_kind == "model":
        result = await db.execute(
            select(Model.id, Model.display_name, Model.model_name).where(Model.id.in_(target_ids))
        )
        return {row.id: (row.display_name or row.model_name) for row in result.all()}
    return {}


async def get_daily_spend(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    target_kind: TargetKind,
    target_id: uuid.UUID | None = None,
    from_date: date_type | None = None,
    to_date: date_type | None = None,
    group_by: GroupBy = "date",
) -> list[dict[str, Any]]:
    """Return aggregated daily spend rows.

    ``user_id`` is always enforced for tenancy: ``user`` axis filters on the
    column directly, ``agent`` axis joins through ``Agent.user_id``, and
    ``model`` axis (catalog-wide) joins through any ``Agent`` owned by the
    user so the totals reflect *that user's* spend on the model.
    """

    from_date, to_date = _resolve_window(from_date, to_date)
    table_cls, target_col = _table_for(target_kind)

    # -- Build base query --------------------------------------------------
    select_cols: list[Any] = [
        table_cls.date.label("date"),
        func.sum(table_cls.total_tokens_in).label("total_tokens_in"),
        func.sum(table_cls.total_tokens_out).label("total_tokens_out"),
        func.sum(table_cls.total_cost_usd).label("total_cost_usd"),
        func.sum(table_cls.request_count).label("request_count"),
    ]
    group_cols: list[Any] = [table_cls.date]
    order_cols: list[Any] = [table_cls.date.asc()]

    if group_by == "target":
        select_cols.append(target_col.label("target_id"))
        group_cols.append(target_col)
        order_cols.append(target_col.asc())

    query = select(*select_cols).where(
        table_cls.date >= from_date,
        table_cls.date <= to_date,
    )

    # Tenancy: scope rows to the current user.
    if target_kind == "user":
        query = query.where(DailySpendUser.user_id == user_id)
    elif target_kind == "agent":
        # 히든 런타임 에이전트(runtime_profile != 'standard')는 일일 집계
        # 표면에서 제외한다 — target축은 에이전트 행 자체가 노출되고, date축도
        # 동일 필터로 축 간 정합을 유지한다 (CHECKPOINT M1 §노출 표면).
        query = query.join(Agent, Agent.id == DailySpendAgent.agent_id).where(
            Agent.user_id == user_id,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
        )
    elif target_kind == "model":
        # Only count rows the user contributed to. We re-filter via the
        # per-agent table because the per-model row aggregates across users.
        # Fold model totals down to (date, model_id) for *this* user via the
        # agent table — daily_spend_model row counts include every user.
        query = (
            select(
                DailySpendAgent.date.label("date"),
                func.sum(DailySpendAgent.total_tokens_in).label("total_tokens_in"),
                func.sum(DailySpendAgent.total_tokens_out).label("total_tokens_out"),
                func.sum(DailySpendAgent.total_cost_usd).label("total_cost_usd"),
                func.sum(DailySpendAgent.request_count).label("request_count"),
            )
            .join(Agent, Agent.id == DailySpendAgent.agent_id)
            .where(
                Agent.user_id == user_id,
                Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                DailySpendAgent.date >= from_date,
                DailySpendAgent.date <= to_date,
            )
            .group_by(DailySpendAgent.date)
            .order_by(DailySpendAgent.date.asc())
        )
        # Override: the model axis is currently scoped through the agent
        # table because it carries the user FK. ``group_by=target`` drills
        # down to ``model_id`` via the model FK on Agent → relationship
        # navigation.
        if group_by == "target":
            query = (
                select(
                    DailySpendAgent.date.label("date"),
                    Agent.model_id.label("target_id"),
                    func.sum(DailySpendAgent.total_tokens_in).label("total_tokens_in"),
                    func.sum(DailySpendAgent.total_tokens_out).label("total_tokens_out"),
                    func.sum(DailySpendAgent.total_cost_usd).label("total_cost_usd"),
                    func.sum(DailySpendAgent.request_count).label("request_count"),
                )
                .join(Agent, Agent.id == DailySpendAgent.agent_id)
                .where(
                    Agent.user_id == user_id,
                    Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                    DailySpendAgent.date >= from_date,
                    DailySpendAgent.date <= to_date,
                )
                .group_by(DailySpendAgent.date, Agent.model_id)
                .order_by(DailySpendAgent.date.asc(), Agent.model_id.asc())
            )
            if target_id is not None:
                query = query.where(Agent.model_id == target_id)
    else:  # group_by == "date"
        if target_id is not None:
            query = query.where(target_col == target_id)
        query = query.group_by(*group_cols).order_by(*order_cols)

    # User/agent axes still need the GROUP BY / ORDER BY when filtered.
    if target_kind in ("user", "agent"):
        if target_id is not None:
            query = query.where(target_col == target_id)
        query = query.group_by(*group_cols).order_by(*order_cols)

    result = await db.execute(query)
    rows = result.all()

    # -- Resolve labels for ``group_by=target`` ----------------------------
    label_lookup: dict[uuid.UUID, str] = {}
    if group_by == "target":
        target_ids = [r.target_id for r in rows if getattr(r, "target_id", None) is not None]
        label_lookup = await _label_map(db, target_kind, list(set(target_ids)))

    # -- Marshal --------------------------------------------------------
    output: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {
            "date": row.date.isoformat() if row.date else None,
            "total_tokens_in": int(row.total_tokens_in or 0),
            "total_tokens_out": int(row.total_tokens_out or 0),
            "total_cost_usd": float(row.total_cost_usd or Decimal("0")),
            "request_count": int(row.request_count or 0),
        }
        if group_by == "target":
            tid = getattr(row, "target_id", None)
            entry["target_id"] = str(tid) if tid is not None else None
            entry["target_label"] = label_lookup.get(tid) if tid is not None else None
        else:
            entry["target_id"] = None
            entry["target_label"] = None
        output.append(entry)
    return output


__all__ = ["GroupBy", "TargetKind", "get_daily_spend"]
