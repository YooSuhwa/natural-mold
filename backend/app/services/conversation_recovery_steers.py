"""Scope steer claims and their public receipts to the chat being recovered."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_input_receipt import text_digest
from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.agent_runtime.recovery_stream import RecoveryOutputStarted
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation_run_steer import ConversationRunSteer
from app.services.conversation_run_steer_events import uses_steer_channel, valid_stored_steer


@dataclass(slots=True)
class RecoverySteers:
    claimed: dict[str, str] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)

    def public_ids(
        self, events: Sequence[dict[str, Any]], conversation_id: UUID, run_id: UUID
    ) -> frozenset[str]:
        published: set[str] = set()
        for raw in events:
            if raw.get("method") != "custom" or not isinstance(raw.get("data"), dict):
                continue
            event = cast(StoredProtocolEvent, raw)
            if not uses_steer_channel(event):
                continue
            required = {"namespace", "id", "upstream_event_id", "thread_id", "run_id"}
            if not required.issubset(raw) or not valid_stored_steer(
                event, conversation_id=conversation_id, run_id=str(run_id)
            ):
                raise RecoveryOutputStarted("Unverified steer event")
            payload = event["data"]["payload"]
            steer_id, phase = payload["steer_id"], payload["status"]
            current = self.status.get(steer_id)
            if current is None or (
                phase != "pending"
                and not (phase == current == "applied" and f"{steer_id}:steer:pending" in published)
            ):
                raise RecoveryOutputStarted("Unverified steer transition")
            published.add(event["id"])
        return frozenset(published)


async def recovery_steers(
    db: AsyncSession, context: OrgContext, *, run_id: UUID, conversation_id: UUID
) -> RecoverySteers:
    row = ConversationRunSteer
    rows = await db.execute(
        select(row.id, row.status, row.text, row.delivery_started_at)
        .where(
            row.run_id == run_id,
            row.conversation_id == conversation_id,
            private_scope_predicate(row, context),
        )
        .order_by(row.sequence)
    )
    proof = RecoverySteers()
    for steer_id, status, text, claimed_at in rows:
        if status not in {"pending", "applied"} or (status == "applied" and claimed_at is None):
            raise RecoveryOutputStarted("Steer requires reconciliation")
        key = str(steer_id)
        proof.status[key] = status
        if claimed_at is not None:
            proof.claimed[key] = text_digest(text)
    return proof
