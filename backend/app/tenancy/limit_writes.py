"""Save usage limits: row locks, If-Match and audit (plan 8.7 "조정", 9.7.1; H-06).

A save locks the company row, then the organization (``lock_tenant_org_write``, the
company-first order of every company/org write), then the agent row for an agent
limit; checks ``If-Match`` against the versioning row (``limit_settings.version_of``;
absent, malformed or stale → ``409 stale_version``); applies the narrowing rule
(``422 limit_exceeds_parent``); bumps that row's ``updated_at``; and records
``policy.limits_update`` with the scope and the before/after limit values. Every save
clears this process's limit cache.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from fastapi import Request
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.authz.usage_limit_access import LimitTarget
from app.authz.usage_limit_queries import org_member
from app.exceptions import ValidationError
from app.models.agent import Agent
from app.models.organization import Organization, Tenant
from app.schemas.usage_limits import UsageLimitUpdate, UsageLimitView
from app.services import audit_service
from app.services.tenants.versioning import bump_timestamp, require_current_version
from app.tenancy.limit_settings import COMPANY_ONLY, limit_view, parent_of, require_within
from app.tenancy.limits import reset_cache

_TARGET_TYPES = {"tenant": "tenant", "org": "organization", "user": "user", "agent": "agent"}


class LimitActor(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def email(self) -> str: ...


@dataclass(frozen=True, slots=True)
class LimitWrite:
    db: AsyncSession
    actor: LimitActor
    request: Request | None
    expected_version: str | None


@dataclass(slots=True)
class _Locked:
    tenant: Tenant
    org: Organization | None
    agent: Agent | None


async def _lock(write: LimitWrite, target: LimitTarget) -> _Locked:
    db = write.db
    orgs = (target.org_id,) if target.org_id else ()
    locked = await lock_tenant_org_write(db, target.tenant_id, orgs)
    org = locked.organizations[0] if target.org_id and locked.organizations else None
    if target.org_id and org is None:
        raise authorization_error("not_found")
    agent = None
    if target.scope == "agent":
        agent = await db.scalar(
            select(Agent)
            .where(Agent.id == target.scope_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if agent is None:
            raise authorization_error("not_found")
    found = _Locked(locked.tenant, org, agent)
    require_current_version(_version(target, found), write.expected_version)
    return found


def _version(target: LimitTarget, rows: _Locked) -> datetime:
    if target.scope == "tenant":
        return rows.tenant.updated_at
    if target.scope == "agent" and rows.agent is not None:
        return rows.agent.updated_at
    assert rows.org is not None  # noqa: S101 - org/user targets always carry the org
    return rows.org.updated_at


def _bump(target: LimitTarget, rows: _Locked) -> None:
    if target.scope == "tenant":
        rows.tenant.updated_at = bump_timestamp(rows.tenant.updated_at)
    elif target.scope == "agent" and rows.agent is not None:
        rows.agent.updated_at = bump_timestamp(rows.agent.updated_at).replace(tzinfo=None)
    elif rows.org is not None:
        rows.org.updated_at = bump_timestamp(rows.org.updated_at)


def _text(value: object) -> JsonValue:
    return None if value is None else str(value)


async def _snapshot(db: AsyncSession, target: LimitTarget, rows: _Locked) -> dict[str, JsonValue]:
    if target.scope == "tenant":
        keys = ("monthly_spend_limit", *COMPANY_ONLY)
        return {key: _text(rows.tenant.limits.get(key)) for key in keys}
    if target.scope == "agent" and rows.agent is not None:
        return {"monthly_spend_limit": _text(rows.agent.monthly_spend_limit)}
    assert rows.org is not None  # noqa: S101 - org/user targets always carry the org
    if target.scope == "org":
        return {
            "monthly_spend_limit": _text(rows.org.monthly_spend_limit),
            "default_member_limit": _text(rows.org.settings.get("default_member_limit")),
        }
    member = await org_member(db, rows.org.id, target.scope_id)
    if member is None:
        raise authorization_error("not_found")
    return {"monthly_spend_limit": _text(member.monthly_spend_limit)}


async def _audit(
    write: LimitWrite,
    target: LimitTarget,
    before: dict[str, JsonValue],
    after: dict[str, JsonValue],
) -> None:
    await audit_service.record_event(
        write.db,
        actor_type="user",
        actor_user_id=write.actor.id,
        actor_email_snapshot=write.actor.email,
        owner_user_id=write.actor.id,
        action=audit_actions.POLICY_LIMITS_UPDATE,
        target_type=_TARGET_TYPES[target.scope],
        target_id=target.scope_id,
        outcome="success",
        request=write.request,
        tenant_id=target.tenant_id,
        org_id=target.org_id,
        metadata={"scope": target.scope, "before": before, "after": after},
    )


def _reject_fields(payload: UsageLimitUpdate, fields: tuple[str, ...]) -> None:
    bad = sorted(set(fields) & payload.model_fields_set)
    if bad:
        raise ValidationError("limit_field_not_allowed", f"이 범위에서 정할 수 없는 값: {bad}")


async def _apply(write: LimitWrite, target: LimitTarget, payload: UsageLimitUpdate) -> None:
    rows = await _lock(write, target)
    db, sent, spend = write.db, payload.model_fields_set, payload.monthly_spend_limit
    if "monthly_spend_limit" in sent:
        require_within(target, spend, parent_of(target, rows.tenant, rows.org))
    before = await _snapshot(db, target, rows)
    if target.scope == "tenant":
        dumped = payload.model_dump(mode="json")
        fields = ("monthly_spend_limit", *COMPANY_ONLY)
        changes = {field: dumped[field] for field in fields if field in sent}
        rows.tenant.limits = {**rows.tenant.limits, **changes}
    elif "monthly_spend_limit" in sent and target.scope == "agent" and rows.agent is not None:
        rows.agent.monthly_spend_limit = spend
    elif "monthly_spend_limit" in sent and target.scope == "org" and rows.org is not None:
        rows.org.monthly_spend_limit = spend
    elif "monthly_spend_limit" in sent and rows.org is not None:
        member = await org_member(db, rows.org.id, target.scope_id)
        if member is None:
            raise authorization_error("not_found")
        member.monthly_spend_limit = spend
    after = await _snapshot(db, target, rows)
    if after != before:
        _bump(target, rows)
        await _audit(write, target, before, after)
    await db.flush()
    reset_cache()


async def save_limits(
    write: LimitWrite, target: LimitTarget, payload: UsageLimitUpdate
) -> UsageLimitView:
    if target.scope != "tenant":
        _reject_fields(payload, COMPANY_ONLY)
    _reject_fields(payload, ("default_member_limit",))  # organization policy: /orgs/{id}/limits
    await _apply(write, target, payload)
    return await limit_view(write.db, target)


async def save_default_member_limit(
    write: LimitWrite, target: LimitTarget, value: Decimal | None
) -> UsageLimitView:
    """The organization policy ``default_member_limit`` (S08, ``can_manage_policy``)."""
    rows = await _lock(write, target)
    if rows.org is None:
        raise authorization_error("not_found")
    member_target = LimitTarget("user", target.scope_id, target.tenant_id, rows.org.id)
    require_within(member_target, value, parent_of(member_target, rows.tenant, rows.org))
    before = await _snapshot(write.db, target, rows)
    rows.org.settings = {**rows.org.settings, "default_member_limit": _text(value)}
    after = await _snapshot(write.db, target, rows)
    if after != before:
        _bump(target, rows)
        await _audit(write, target, before, after)
    await write.db.flush()
    reset_cache()
    return await limit_view(write.db, target)


__all__ = ["LimitWrite", "save_default_member_limit", "save_limits"]
