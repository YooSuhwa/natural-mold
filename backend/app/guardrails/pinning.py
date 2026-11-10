"""Pin a chat run's guardrail rule set on its run row (plan §3.4, ADR-024 §4).

A new turn resolves the company policy once and stores the result as
``conversation_runs.metadata_json["guardrails"]``; a resume (an approval answered, a stream
re-attached) reuses the conversation's latest pinned snapshot, so a policy change applies
from the next turn and never to a run already in progress. ``{"rules": null}`` pins "off".
The company's tool risk actions are pinned with them (``"tool_risk"``, null = defaults),
and so are its per-tool policies (``"tool_policies"``). One thing is not held to the pin: a
tool the company switched off since is off for a resumed run too (tool-governance-plan 3.11).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_run_scope import private_run_predicate
from app.authz.org_context import OrgContext
from app.guardrails.policy import disabled_tools, resolve, resolve_tool_risk
from app.guardrails.rules import RuleSet
from app.guardrails.templates import rules_to_json, snapshot_from_json
from app.guardrails.tool_risk import (
    DEFAULT_ACTIONS,
    InvalidToolRiskError,
    ToolPolicy,
    ToolRiskPolicy,
    policy_from_actions,
    tool_policies_from_json,
)
from app.models.conversation_run import ConversationRun

RUN_METADATA_KEY: Final = "guardrails"
_RECENT_RUNS: Final = 20


@dataclass(frozen=True, slots=True)
class PinnedGuardrails:
    rules: RuleSet | None
    tool_risk: ToolRiskPolicy | None


def _snapshot(rules: RuleSet | None, tool_risk: ToolRiskPolicy | None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "rules": rules_to_json(rules) if rules is not None else None,
        "tool_risk": dict(tool_risk.actions) if tool_risk is not None else None,
    }
    if tool_risk is not None and tool_risk.always_ask:  # stored only when set (shape kept)
        snapshot["always_ask_tools"] = sorted(tool_risk.always_ask)
    if tool_risk is not None and tool_risk.tools:
        snapshot["tool_policies"] = {
            key: value.to_json() for key, value in sorted(tool_risk.tools.items())
        }
    return snapshot


def _from_snapshot(record: dict[str, Any]) -> PinnedGuardrails:
    rules = record.get("rules")
    tool_risk = record.get("tool_risk")
    always = record.get("always_ask_tools")
    always_ask = (
        frozenset(str(name) for name in always) if isinstance(always, list) else frozenset()
    )
    try:
        tools = tool_policies_from_json(record.get("tool_policies"))
    except InvalidToolRiskError:
        # Our own snapshot, unreadable: every tool with a policy is off rather than open.
        raw = record.get("tool_policies")
        tools = {str(key): _OFF for key in raw} if isinstance(raw, dict) else {}
    return PinnedGuardrails(
        snapshot_from_json(rules) if isinstance(rules, dict) else None,
        policy_from_actions(tool_risk, always_ask, tools) if isinstance(tool_risk, dict) else None,
    )


_OFF: Final = ToolPolicy(False, None, None, None)


def _with_off(pinned: PinnedGuardrails, off: frozenset[str]) -> PinnedGuardrails:
    """The pinned policy with the tools the company has switched off since."""
    if not off:
        return pinned
    current = pinned.tool_risk
    tools = dict(current.tools) if current else {}
    for key in off:
        found = tools.get(key)
        tools[key] = replace(found, enabled=False) if found else _OFF
    return PinnedGuardrails(
        pinned.rules,
        ToolRiskPolicy(
            dict(current.actions) if current else dict(DEFAULT_ACTIONS),
            current.always_ask if current else frozenset(),
            tools,
        ),
    )


async def _latest_pinned(
    db: AsyncSession, run_id: uuid.UUID, conversation_id: uuid.UUID, context: OrgContext
) -> dict[str, Any] | None:
    runs = await db.scalars(
        select(ConversationRun)
        .where(
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.id != run_id,
            private_run_predicate(context),
        )
        .order_by(ConversationRun.created_at.desc(), ConversationRun.id.desc())
        .limit(_RECENT_RUNS)
    )
    for run in runs:
        record = (run.metadata_json or {}).get(RUN_METADATA_KEY)
        if isinstance(record, dict):
            return record
    return None


async def pin_run_guardrails(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    context: OrgContext,
    agent_id: uuid.UUID | None,
    runtime_profile: str,
    resume: bool,
) -> PinnedGuardrails:
    record = await _latest_pinned(db, run_id, conversation_id, context) if resume else None
    reused = record is not None
    if record is None:
        record = _snapshot(
            await resolve(db, context.tenant_id, agent_id, runtime_profile),
            await resolve_tool_risk(db, context.tenant_id, runtime_profile),
        )
    run = await db.scalar(
        select(ConversationRun).where(ConversationRun.id == run_id, private_run_predicate(context))
    )
    if run is not None:
        run.metadata_json = {**(run.metadata_json or {}), RUN_METADATA_KEY: record}
        await db.commit()
    pinned = _from_snapshot(record)
    if reused and runtime_profile == "standard":
        return _with_off(pinned, await disabled_tools(db, context.tenant_id))
    return pinned


async def run_guardrails(
    db: AsyncSession,
    run: ConversationRun,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    runtime_profile: str,
) -> PinnedGuardrails:
    """What this run was pinned to, else the company policy now (nothing is written).

    The prepared lease reads it inside its own locked transaction: a chat turn was pinned
    before the lease started; a scheduled or Agent API run resolves here.
    """
    record = (run.metadata_json or {}).get(RUN_METADATA_KEY)
    if isinstance(record, dict):
        return _from_snapshot(record)
    return PinnedGuardrails(
        await resolve(db, tenant_id, agent_id, runtime_profile),
        await resolve_tool_risk(db, tenant_id, runtime_profile),
    )
