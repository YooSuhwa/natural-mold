"""Journal ordinary chat tool dispatch under the run's current owner lock."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langgraph.errors import GraphBubbleUp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat_recovery.fence import RunFence, lock_run_fence, open_run_session
from app.models.conversation_run import ConversationRunToolReceipt


class ToolRecoveryBlocked(GraphBubbleUp):
    """Abort the graph; a tool error message could invite an unsafe second attempt."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolDispatch:
    call_id: str
    tool_name: str
    arguments_sha256: str
    read_only: bool

    @property
    def effect_kind(self) -> str:
        return "read" if self.read_only else "external_write"


async def reserve_dispatch(fence: RunFence, call: ToolDispatch) -> dict[str, Any] | None:
    """Commit intent before invoking the tool; serialize with both dispatch and handoff."""
    async with open_run_session(fence) as db:
        run = await lock_run_fence(db, fence)
        receipt = await db.get(ConversationRunToolReceipt, (run.id, call.call_id))
        if receipt is None:
            if fence.generation > 1 and not call.read_only:
                raise ToolRecoveryBlocked("recovery_tool_not_allowed")
            db.add(
                ConversationRunToolReceipt(
                    tenant_id=run.tenant_id,
                    org_id=run.org_id,
                    run_id=run.id,
                    tool_call_id=call.call_id,
                    tool_name=call.tool_name,
                    arguments_sha256=call.arguments_sha256,
                    effect_kind=call.effect_kind,
                    owner_generation=fence.generation,
                    status="started",
                )
            )
        else:
            if (
                receipt.tool_name != call.tool_name
                or receipt.arguments_sha256 != call.arguments_sha256
                or receipt.effect_kind != call.effect_kind
            ):
                raise ToolRecoveryBlocked("tool_receipt_conflict")
            if not call.read_only:
                # Even a completed write may return data protected by a leaf credential
                # check. The recovery path only reuses reviewed credential-free reads.
                raise ToolRecoveryBlocked("recovery_effect_unconfirmed")
            if receipt.status == "completed":
                if not isinstance(receipt.result_json, dict):
                    raise ToolRecoveryBlocked("tool_result_unavailable")
                return receipt.result_json
            if receipt.status != "started" or receipt.owner_generation >= fence.generation:
                raise ToolRecoveryBlocked("tool_call_in_progress")
            receipt.owner_generation = fence.generation
        await db.commit()
        return None


async def confirm_dispatch(
    fence: RunFence, call: ToolDispatch, result: dict[str, Any] | None
) -> None:
    async with open_run_session(fence) as db:
        run = await lock_run_fence(db, fence)
        receipt = await db.get(ConversationRunToolReceipt, (run.id, call.call_id))
        if (
            receipt is None
            or receipt.owner_generation != fence.generation
            or receipt.status != "started"
            or receipt.arguments_sha256 != call.arguments_sha256
        ):
            raise ToolRecoveryBlocked("tool_receipt_conflict")
        receipt.result_json = result
        receipt.status = "completed"
        await db.commit()


async def blocking_receipt_code(
    db: AsyncSession, run_id: UUID, *, completed_call_ids: Collection[str] = ()
) -> str | None:
    """Run inside the scoped takeover transaction with the run row locked."""
    receipt = ConversationRunToolReceipt
    # Only presence is required. Avoid loading a run's entire history of result payloads.
    rows = await db.execute(
        select(
            receipt.tool_call_id, receipt.effect_kind, receipt.status, receipt.result_json.is_(None)
        ).where(receipt.run_id == run_id)
    )
    confirmed = set(completed_call_ids)
    missing_read = False
    for call_id, effect, status, missing_result in rows:
        if effect != "read":
            if status != "completed" or call_id not in confirmed:
                return "recovery_effect_unconfirmed"
        elif status == "completed" and missing_result and call_id not in confirmed:
            missing_read = True
    return "tool_result_unavailable" if missing_read else None
