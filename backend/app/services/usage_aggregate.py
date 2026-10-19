"""Read-side service for the daily spend aggregate tables.

The spend writer rolls every agent invocation into ``daily_spend_user``,
``daily_spend_agent``, ``daily_spend_model``. This service exposes the
aggregated time-series rows the dashboard renders (agent/model axes come from the
user's own ``token_usages``, F-14). The shape mirrors what
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

from app.models.agent import Agent
from app.models.daily_spend_user import DailySpendUser
from app.models.model import Model
from app.models.user import User
from app.services.usage_caller_breakdown import own_daily_rows

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

    today = date_type.today()  # noqa: DTZ011 — dashboard window in server-local calendar days
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


resolve_window = _resolve_window  # shared with the org usage views (H-03)


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


def _iso(value: object) -> str | None:
    """Aggregate tables return ``date``; ``func.date`` on SQLite returns ``'YYYY-MM-DD'``."""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date_type) else str(value)


async def _user_rows(
    db: AsyncSession,
    user_id: uuid.UUID,
    target_id: uuid.UUID | None,
    from_date: date_type,
    to_date: date_type,
    group_by: GroupBy,
) -> list[Any]:
    """``daily_spend_user`` rows for the current user (the billed requester)."""
    table = DailySpendUser
    columns: list[Any] = [
        table.date.label("date"),
        func.sum(table.total_tokens_in).label("total_tokens_in"),
        func.sum(table.total_tokens_out).label("total_tokens_out"),
        func.sum(table.total_cost_usd).label("total_cost_usd"),
        func.sum(table.request_count).label("request_count"),
    ]
    groups: list[Any] = [table.date]
    if group_by == "target":
        columns.append(table.user_id.label("target_id"))
        groups.append(table.user_id)
    query = select(*columns).where(
        table.date >= from_date, table.date <= to_date, table.user_id == user_id
    )
    if target_id is not None:
        query = query.where(table.user_id == target_id)
    query = query.group_by(*groups).order_by(*(group.asc() for group in groups))
    return list((await db.execute(query)).all())


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

    ``user_id`` is always enforced: the ``user`` axis reads ``daily_spend_user``;
    ``agent`` / ``model`` axes read the user's own billed ``token_usages`` rows
    (``usage_caller_breakdown``, F-14), so every axis is *that user's* spend.
    """

    from_date, to_date = _resolve_window(from_date, to_date)
    if target_kind == "user":
        rows = await _user_rows(db, user_id, target_id, from_date, to_date, group_by)
    else:
        # F-14: agent/model breakdowns are the user's own billed usage (caller_user_id),
        # so they add up to the user total and never show other callers' cost.
        rows = await own_daily_rows(
            db,
            user_id=user_id,
            axis=target_kind,
            target_id=target_id,
            from_date=from_date,
            to_date=to_date,
            by_target=group_by == "target",
        )

    # -- Resolve labels for ``group_by=target`` ----------------------------
    label_lookup: dict[uuid.UUID, str] = {}
    if group_by == "target":
        target_ids = [r.target_id for r in rows if getattr(r, "target_id", None) is not None]
        label_lookup = await _label_map(db, target_kind, list(set(target_ids)))

    # -- Marshal --------------------------------------------------------
    output: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {
            "date": _iso(row.date),
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
