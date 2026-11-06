"""Pin a chat run's guardrail rule set on its run row (plan §3.4, ADR-024 §4).

A new turn resolves the company policy once and stores the result as
``conversation_runs.metadata_json["guardrails"]``; a resume (an approval answered, a stream
re-attached) reuses the conversation's latest pinned snapshot, so a policy change applies
from the next turn and never to a run already in progress. ``{"rules": null}`` pins "off".
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_run_scope import private_run_predicate
from app.authz.org_context import OrgContext
from app.guardrails.policy import resolve
from app.guardrails.rules import RuleSet
from app.guardrails.templates import rules_to_json, snapshot_from_json
from app.models.conversation_run import ConversationRun

RUN_METADATA_KEY: Final = "guardrails"
_RECENT_RUNS: Final = 20


def _snapshot(rules: RuleSet | None) -> dict[str, Any]:
    return {"rules": rules_to_json(rules) if rules is not None else None}


def _from_snapshot(record: dict[str, Any]) -> RuleSet | None:
    rules = record.get("rules")
    return snapshot_from_json(rules) if isinstance(rules, dict) else None


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
) -> RuleSet | None:
    record = await _latest_pinned(db, run_id, conversation_id, context) if resume else None
    if record is None:
        record = _snapshot(await resolve(db, context.tenant_id, agent_id, runtime_profile))
    run = await db.scalar(
        select(ConversationRun).where(ConversationRun.id == run_id, private_run_predicate(context))
    )
    if run is not None:
        run.metadata_json = {**(run.metadata_json or {}), RUN_METADATA_KEY: record}
        await db.commit()
    return _from_snapshot(record)
