"""Who may register a tool, an MCP server, a skill or an agent (tool-governance-plan 3.6).

Company setting ``tenants.settings["registration"]``, one rule per resource kind:

- ``who``: ``members`` (every member, today's behaviour) or ``granted`` (only people who
  hold the kind's capability: organization builder/admin, a capability grant, or a
  company administrator).
- ``review``: ``on`` sends what a person *without* the capability registers to an
  administrator before anyone can use it (3.7). Only kinds with a registration state.
- ``member_sharing``: ``private`` keeps what such a person registered to themselves.

The capability is read from the database whatever the authorization mode is, the way
organization-wide sharing does; the routes' own capability gates (which the authorization
service decides in enforce mode) stay as they are. ``admit`` is called where the row is
made (the create services, blueprint import, marketplace install, builders), so no path
reaches a new row around it. A value that cannot be read is the strict rule: what the
company restricted must not open up because its value went bad.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.org_context import OrgContext
from app.authz.org_permissions import organization_permissions
from app.models.organization import Tenant, TenantRoleGrant

SETTINGS_KEY: Final = "registration"
KINDS: Final = ("mcp_server", "tool", "skill", "agent")
CAPABILITY: Final = {
    "mcp_server": "cap_register_mcp",
    "tool": "cap_create_tool",
    "skill": "cap_create_skill",
    "agent": "cap_create_agent",
}
# Kinds whose rows carry a registration state (3.7); the others cannot be held for review.
REVIEWED: Final = frozenset({"mcp_server", "tool"})
WHO: Final = ("members", "granted")
REVIEW: Final = ("off", "on")
SHARING: Final = ("shareable", "private")


@dataclass(frozen=True, slots=True)
class Rule:
    who: str = "members"
    review: str = "off"
    member_sharing: str = "shareable"


DEFAULT: Final = Rule()
STRICT: Final = Rule("granted", "on", "private")


@dataclass(frozen=True, slots=True)
class Registration:
    """The answer for one creation: allowed, and whether it waits for an administrator."""

    rule: Rule
    needs_review: bool


def _rule(kind: str, raw: Any) -> Rule:
    if raw is None:
        return DEFAULT
    if not isinstance(raw, Mapping):
        return STRICT
    who = raw.get("who", DEFAULT.who)
    review = raw.get("review", DEFAULT.review)
    sharing = raw.get("member_sharing", DEFAULT.member_sharing)
    if who not in WHO or review not in REVIEW or sharing not in SHARING:
        return STRICT
    return Rule(who, review if kind in REVIEWED else "off", sharing)


def rules_from_settings(settings: Any) -> dict[str, Rule]:
    """Every kind's rule; nothing saved is the default, an unreadable value is strict."""
    raw = settings.get(SETTINGS_KEY) if isinstance(settings, Mapping) else None
    if raw is None:
        return dict.fromkeys(KINDS, DEFAULT)
    if not isinstance(raw, Mapping):
        return dict.fromkeys(KINDS, STRICT)
    return {kind: _rule(kind, raw.get(kind)) for kind in KINDS}


def rules_to_settings(rules: Mapping[str, Rule]) -> dict[str, dict[str, str]]:
    return {
        kind: {
            "who": rules[kind].who,
            "review": rules[kind].review,
            "member_sharing": rules[kind].member_sharing,
        }
        for kind in KINDS
    }


async def rule_for(db: AsyncSession, ctx: OrgContext, kind: str) -> Rule:
    settings = await db.scalar(select(Tenant.settings).where(Tenant.id == ctx.tenant_id))
    return rules_from_settings(settings)[kind]


async def holds(db: AsyncSession, ctx: OrgContext, kind: str) -> bool:
    """Whether the caller registers for the company: the capability or company admin."""
    _, capabilities = await organization_permissions(db, ctx.org_id, ctx.user_id)
    if CAPABILITY[kind] in capabilities:
        return True
    admin = await db.scalar(
        select(TenantRoleGrant.user_id).where(
            TenantRoleGrant.tenant_id == ctx.tenant_id,
            TenantRoleGrant.user_id == ctx.user_id,
            TenantRoleGrant.role == "admin",
        )
    )
    return admin is not None


async def admit(db: AsyncSession, ctx: OrgContext, kind: str) -> Registration:
    """Allow the creation or raise 403 ``capability_required``."""
    # Reading the rule must not flush what the caller has staged: a creation refused
    # later (a private binding, say) leaves the session as it found it.
    with db.no_autoflush:
        rule = await rule_for(db, ctx, kind)
        if rule == DEFAULT:
            return Registration(rule, needs_review=False)
        holder = await holds(db, ctx, kind)
    if rule.who == "granted" and not holder:
        raise denials.capability_required(CAPABILITY[kind])
    return Registration(rule, needs_review=rule.review == "on" and not holder)


async def review_needed(db: AsyncSession, ctx: OrgContext, kind: str) -> bool:
    """Whether what ``ctx.user_id`` registers of this kind waits for an administrator."""
    if kind not in REVIEWED:
        return False
    with db.no_autoflush:
        rule = await rule_for(db, ctx, kind)
        if rule.review != "on":
            return False
        return not await holds(db, ctx, kind)


async def is_company_admin(db: AsyncSession, tenant_id: Any, user_id: Any) -> bool:
    admin = await db.scalar(
        select(TenantRoleGrant.user_id).where(
            TenantRoleGrant.tenant_id == tenant_id,
            TenantRoleGrant.user_id == user_id,
            TenantRoleGrant.role == "admin",
        )
    )
    return admin is not None


async def decider_scope(db: AsyncSession, ctx: OrgContext) -> str | None:
    """Whose registration requests the caller decides (plan G9).

    ``company``: every organization's (company administrator). ``organization``: the
    current organization's (its administrator). ``None``: nobody's.
    """
    if await is_company_admin(db, ctx.tenant_id, ctx.user_id):
        return "company"
    roles, _ = await organization_permissions(db, ctx.org_id, ctx.user_id)
    return "organization" if "admin" in roles else None


async def require_shareable(db: AsyncSession, ctx: OrgContext, kind: str) -> None:
    """``member_sharing: private``: only someone who registers for the company shares."""
    if kind not in CAPABILITY:
        return
    with db.no_autoflush:
        rule = await rule_for(db, ctx, kind)
        if rule.member_sharing == "private" and not await holds(db, ctx, kind):
            raise denials.capability_required(CAPABILITY[kind])
