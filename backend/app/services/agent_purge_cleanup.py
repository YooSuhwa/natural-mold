"""Idempotent external cleanup for committed Agent purge receipts."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, assert_never
from uuid import UUID

from anyio.to_thread import run_sync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.runtime_config import runtime_data_dir
from app.models.agent_purge import AgentPurgeCleanupItem, AgentPurgeReceipt
from app.models.conversation import Conversation
from app.models.conversation_run import utc_now_naive
from app.services.agent_image_paths import agent_image_dir
from app.services.agent_purge_history_validation import verified_manifests
from app.services.conversation_history_snapshot import HistorySnapshotIncompleteError

logger = logging.getLogger(__name__)
type CleanupKind = Literal["agent_image", "agent_files", "raw_checkpoint"]
_CLEANUP_KINDS: Final[dict[str, CleanupKind]] = {
    "agent_image": "agent_image",
    "agent_files": "agent_files",
    "raw_checkpoint": "raw_checkpoint",
}


@dataclass(frozen=True, slots=True)
class InvalidCleanupItemError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path)


def _cleanup_kind(raw: str) -> CleanupKind:
    try:
        return _CLEANUP_KINDS[raw]
    except KeyError as error:
        raise InvalidCleanupItemError(f"unsupported cleanup kind: {raw}") from error


def _owned_agent_root(base: Path, agent_id: UUID) -> Path:
    configured_root = base.absolute()
    expected = configured_root / str(agent_id)
    if expected.resolve(strict=False) != configured_root.resolve(strict=False) / str(agent_id):
        raise InvalidCleanupItemError("cleanup Agent root crosses a symlink boundary")
    return expected


def _matches_resolved_path(target: Path, expected: Path) -> bool:
    return target.resolve(strict=False) == expected.resolve(strict=False)


async def _execute_cleanup_item(
    db: AsyncSession,
    receipt: AgentPurgeReceipt,
    item: AgentPurgeCleanupItem,
) -> None:
    if sha256(item.locator.encode()).hexdigest() != item.locator_sha256:
        raise InvalidCleanupItemError("cleanup locator checksum mismatch")
    kind = _cleanup_kind(item.kind)
    match kind:
        case "agent_image":
            target = Path(item.locator)
            expected_parent = _owned_agent_root(agent_image_dir(), receipt.agent_id_snapshot)
            if (
                target.parent != expected_parent
                or not target.name.startswith("avatar.")
                or not _matches_resolved_path(target, expected_parent / target.name)
            ):
                raise InvalidCleanupItemError("cleanup image locator is outside the Agent root")
            await run_sync(_remove_path, target)
        case "agent_files":
            target = Path(item.locator)
            expected = _owned_agent_root(runtime_data_dir() / "agents", receipt.agent_id_snapshot)
            if target != expected or not _matches_resolved_path(target, expected):
                raise InvalidCleanupItemError("cleanup file locator is outside the Agent roots")
            await run_sync(_remove_path, target)
        case "raw_checkpoint":
            conversation_id = UUID(item.locator)
            conversation = await db.scalar(
                select(Conversation.id).where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == receipt.tenant_id,
                    Conversation.org_id == receipt.org_id,
                    Conversation.agent_id.is_(None),
                    Conversation.agent_lifecycle_state == "purged",
                )
            )
            manifests = await verified_manifests(db, receipt.agent_id_snapshot, (conversation_id,))
            if conversation is None or manifests is None:
                raise HistorySnapshotIncompleteError("raw checkpoint has no complete manifest")
            saver = checkpointer.get_checkpointer()
            await saver.adelete_thread(item.locator)
        case unreachable:
            assert_never(unreachable)


async def process_agent_purge_cleanup(
    db: AsyncSession,
    receipt_id: UUID,
) -> AgentPurgeReceipt:
    """Retry failed/pending cleanup items and persist only redacted failures."""
    receipt = await db.scalar(
        select(AgentPurgeReceipt)
        .where(AgentPurgeReceipt.id == receipt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if receipt is None:
        raise LookupError("purge receipt is unavailable")
    if receipt.state == "completed":
        return receipt
    items = tuple(
        await db.scalars(
            select(AgentPurgeCleanupItem)
            .where(AgentPurgeCleanupItem.receipt_id == receipt.id)
            .order_by(AgentPurgeCleanupItem.kind, AgentPurgeCleanupItem.locator)
            .with_for_update()
        )
    )
    now: datetime = utc_now_naive()
    for item in items:
        if item.status == "succeeded":
            continue
        item.attempts += 1
        try:
            await _execute_cleanup_item(db, receipt, item)
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - durable external boundary
            logger.warning(
                "Agent purge cleanup item failed receipt=%s kind=%s",
                receipt.id,
                item.kind,
            )
            item.status = "failed"
            item.last_error = "external cleanup failed"
            item.completed_at = None
        else:
            item.status = "succeeded"
            item.last_error = None
            item.completed_at = now
    failed = any(item.status != "succeeded" for item in items)
    receipt.state = "cleanup_pending" if failed else "completed"
    receipt.failure_code = "EXTERNAL_CLEANUP_FAILED" if failed else None
    receipt.failure_message = "One or more cleanup items require retry" if failed else None
    receipt.completed_at = None if failed else now
    await db.flush()
    return receipt


__all__ = ["process_agent_purge_cleanup"]
