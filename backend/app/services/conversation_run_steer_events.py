"""Project committed steer receipts through the run's single ordered stream writer."""

from __future__ import annotations

import logging
import time
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime import event_names
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_custom_protocol_event
from app.authz.conversation_scope import can_read_conversation_run_id, require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.config import settings
from app.database import tenant_session
from app.exceptions import AppError
from app.models.conversation_run_steer import ConversationRunSteer

logger = logging.getLogger(__name__)


class RunSteerReceiptFeed:
    def __init__(self, context: OrgContext, conversation_id: UUID, run_id: UUID) -> None:
        self.context = context
        self.conversation_id = conversation_id
        self.run_id = run_id
        self._last_sequence = 0
        self._pending: set[UUID] = set()
        self._next_read = 0.0

    @classmethod
    def for_chat(
        cls, context: OrgContext, thread_id: str, run_id: str | None, *, source: str, profile: str
    ) -> RunSteerReceiptFeed | None:
        if not settings.chat_steer_enabled or not run_id or profile != "standard":
            return None
        if source not in {"chat", "start", "resume", "edit", "regenerate", "recovery"}:
            return None
        try:
            return cls(context, UUID(thread_id), UUID(run_id))
        except ValueError:
            return None

    async def changed_events(
        self, *, first_seq: int, force: bool = False
    ) -> list[StoredProtocolEvent]:
        """Read committed changes, never instruction text. GET also covers silent/finished runs."""
        now = time.monotonic()
        if not force and now < self._next_read:
            return []
        self._next_read = now + 1.0

        steer = ConversationRunSteer
        changed = steer.sequence > self._last_sequence
        if self._pending:
            changed = or_(changed, steer.id.in_(self._pending) & (steer.status != "pending"))
        stmt = (
            select(steer.id, steer.sequence, steer.status)
            .where(
                steer.run_id == self.run_id,
                steer.conversation_id == self.conversation_id,
                private_scope_predicate(steer, self.context),
                changed,
            )
            .order_by(steer.sequence)
        )
        try:
            async with tenant_session(self.context) as db:
                rows = (await db.execute(stmt)).all()
                if rows:
                    conversation = await require_private_conversation(
                        db, self.conversation_id, self.context
                    )
                    if not await can_read_conversation_run_id(
                        db, conversation, self.run_id, self.context
                    ):
                        return []
        except AppError:
            return []
        except SQLAlchemyError as exc:
            # Stream progress is independent of a transient receipt-read failure.
            logger.warning(
                "Steer receipt read failed for run %s (%s)", self.run_id, type(exc).__name__
            )
            return []

        # A single snapshot may cover both admission and application, even on hosts
        # with different wall clocks. The per-conversation sequence orders each phase.
        admissions: list[tuple[UUID, str]] = []
        decisions: list[tuple[UUID, str]] = []
        for row in rows:
            if row.sequence > self._last_sequence:
                admissions.append((row.id, "pending"))
                self._last_sequence = row.sequence
            if row.status == "pending":
                self._pending.add(row.id)
            else:
                self._pending.discard(row.id)
                decisions.append((row.id, row.status))

        return [
            stored_custom_protocol_event(
                run_id=str(self.run_id),
                thread_id=str(self.conversation_id),
                seq=first_seq + index,
                name=event_names.RUN_STEER,
                payload={
                    "conversation_id": str(self.conversation_id),
                    "run_id": str(self.run_id),
                    "steer_id": str(steer_id),
                    "status": status,
                },
                event_id=f"{steer_id}:steer:{status}",
            )
            for index, (steer_id, status) in enumerate(admissions + decisions)
        ]
