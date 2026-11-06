"""Guardrail records: detection rows, block audits and Langfuse spans (plan §3.5).

* ``guardrail_events``: one row per check, stage, rule, entity and action with a count and
  the company-keyed value digests. Never the text or the value.
* Audit: a ``guardrail.block`` event per check that blocked (plan §3.5: blocks and approval
  switches are audited; masking and warnings are detection records only).
* Langfuse: a ``guardrail`` observation per check on the run's trace, with its duration.

Writes are best effort and batched per hook: a record that cannot be written is logged and
never fails the run.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.database import TenantScoped, tenant_session
from app.guardrails.actions import Applied
from app.guardrails.rules import RuleSet, Stage
from app.models.conversation import Conversation
from app.models.guardrail_event import GuardrailEvent
from app.services import audit_service

logger = logging.getLogger(__name__)

_MAX_DIGESTS = 20
_AUDIT_FIELDS = ("template", "template_version", "stage", "entity")


@dataclass(frozen=True, slots=True)
class RunScope:
    """Where a run's records go; ``org_context`` None means nothing is written."""

    org_context: TenantScoped | None
    conversation_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    run_id: str | None = None


def parse_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def value_digest(key: bytes, entity: str, value: str) -> str:
    return hmac.new(key, f"{entity}\x00{value}".encode(), hashlib.sha256).hexdigest()[:16]


@dataclass
class EventLog:
    scope: RunScope
    rows: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        rules: RuleSet,
        stage: Stage,
        texts: Sequence[str],
        results: Sequence[Applied],
        key: bytes,
    ) -> None:
        counts: Counter[tuple[str, str, str]] = Counter()
        digests: dict[tuple[str, str, str], list[str]] = {}
        for text, result in zip(texts, results, strict=True):
            for d in result.decisions:
                group = (d.rule, d.entity, d.action)
                counts[group] += 1
                found = digests.setdefault(group, [])
                digest = value_digest(key, d.entity, text[d.start : d.end])
                if digest not in found and len(found) < _MAX_DIGESTS:
                    found.append(digest)
        for (rule, entity, action), count in sorted(counts.items()):
            row = {
                "template": rules.template,
                "template_version": rules.version,
                "stage": stage,
                "rule": rule,
                "entity": entity,
                "action": action,
                "count": count,
                "value_digests": digests[(rule, entity, action)],
            }
            self.rows.append(row)
            if action == "block":
                self.blocks.append(row)

    async def flush(self) -> None:
        context = self.scope.org_context
        rows, blocks = self.rows, self.blocks
        self.rows, self.blocks = [], []
        if context is None or not rows:
            return
        try:
            async with tenant_session(context) as db:
                conversation_id = self.scope.conversation_id
                if (
                    conversation_id is not None
                    and await db.get(Conversation, conversation_id) is None
                ):
                    conversation_id = None  # an API or trigger thread id, not a conversation row
                db.add_all(
                    GuardrailEvent(
                        tenant_id=context.tenant_id,
                        conversation_id=conversation_id,
                        agent_id=self.scope.agent_id,
                        run_id=self.scope.run_id,
                        **row,
                    )
                    for row in rows
                )
                for row in blocks:
                    await audit_service.record_event(
                        db,
                        actor_type="system",
                        action="guardrail.block",
                        target_type="conversation",
                        target_id=conversation_id,
                        outcome="denied",
                        reason_code=row["rule"],
                        run_id=self.scope.run_id,
                        tenant_id=context.tenant_id,
                        metadata={k: row[k] for k in _AUDIT_FIELDS},
                    )
                await db.commit()
        except Exception:  # noqa: BLE001 - records never fail the run
            logger.warning("guardrail records not written (%d rows)", len(rows), exc_info=True)


@contextmanager
def trace(run_id: str | None, stage: Stage, template: str) -> Iterator[dict[str, Any]]:
    """A ``guardrail`` observation on the run's trace; yields a dict the caller fills."""
    from app.observability import langfuse

    summary: dict[str, Any] = {"stage": stage, "template": template}
    started = time.perf_counter()
    span_cm = None
    if run_id is not None and langfuse.is_langfuse_enabled():
        try:
            client = langfuse._get_langfuse_client()  # noqa: SLF001 - same package's client cache
            span_cm = client.start_as_current_observation(
                as_type="guardrail",
                name=f"guardrail.{stage}",
                trace_context={"trace_id": langfuse._trace_id_for_run(client, run_id)},  # noqa: SLF001
            )
        except Exception:  # noqa: BLE001 - tracing never fails the run
            logger.debug("guardrail span not started", exc_info=True)
    if span_cm is None:
        yield summary
        return
    with span_cm as span:
        yield summary
        summary["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        try:
            span.update(metadata=summary)
        except Exception:  # noqa: BLE001
            logger.debug("guardrail span not updated", exc_info=True)
