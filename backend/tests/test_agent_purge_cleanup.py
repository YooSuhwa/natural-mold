from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from anyio import Path as AsyncPath
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_purge import AgentPurgeCleanupItem
from app.models.conversation_history import ConversationHistoryManifest
from app.services import agent_purge_cleanup
from app.services.agent_purge_cleanup import process_agent_purge_cleanup
from tests.agent_purge_cleanup_cases import (
    RecordingSaver,
    seed_cleanup_receipt,
    write_outside_file,
)


async def test_unavailable_checkpoint_store_stays_cleanup_pending(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: DB deletion committed and a complete manifest authorizes raw cleanup.
    conversation_id = uuid4()
    receipt, item = await seed_cleanup_receipt(db, "raw_checkpoint", str(conversation_id))
    monkeypatch.setattr(
        agent_purge_cleanup.checkpointer,
        "get_checkpointer",
        lambda: (_ for _ in ()).throw(RuntimeError("offline secret detail")),
    )

    # When: the backing checkpointer is unavailable.
    result = await process_agent_purge_cleanup(db, receipt.id)
    await db.commit()

    # Then: absence is never fabricated and only a stable redacted error is stored.
    assert result.state == "cleanup_pending"
    stored = await db.get(AgentPurgeCleanupItem, item.id)
    assert stored is not None
    assert (stored.status, stored.attempts, stored.last_error) == (
        "failed",
        1,
        "external cleanup failed",
    )
    assert "offline" not in (result.failure_message or "")


async def test_failed_checkpoint_cleanup_replays_same_item_once(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one prior cleanup attempt failed after relational deletion.
    conversation_id = uuid4()
    receipt, item = await seed_cleanup_receipt(db, "raw_checkpoint", str(conversation_id))
    item.status = "failed"
    item.attempts = 1
    item.last_error = "external cleanup failed"
    receipt.state = "cleanup_pending"
    await db.commit()
    saver = RecordingSaver()
    monkeypatch.setattr(agent_purge_cleanup.checkpointer, "get_checkpointer", lambda: saver)

    # When: the same receipt is replayed.
    result = await process_agent_purge_cleanup(db, receipt.id)
    await db.commit()

    # Then: the existing item completes without replacement or duplicate calls.
    assert result.state == "completed"
    stored = await db.get(AgentPurgeCleanupItem, item.id)
    assert stored is not None
    assert (stored.id, stored.status, stored.attempts) == (item.id, "succeeded", 2)
    assert saver.deleted_threads == [str(conversation_id)]


async def test_checkpoint_cleanup_requires_matching_manifest_digest(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a cleanup item references a complete-looking but corrupted manifest.
    conversation_id = uuid4()
    receipt, item = await seed_cleanup_receipt(db, "raw_checkpoint", str(conversation_id))
    manifest = await db.scalar(
        select(ConversationHistoryManifest).where(
            ConversationHistoryManifest.conversation_id == conversation_id
        )
    )
    assert manifest is not None
    manifest.canonical_payload_sha256 = "f" * 64
    await db.commit()
    saver = RecordingSaver()
    monkeypatch.setattr(agent_purge_cleanup.checkpointer, "get_checkpointer", lambda: saver)

    # When: cleanup revalidates the retained graph before deleting raw state.
    result = await process_agent_purge_cleanup(db, receipt.id)
    await db.commit()

    # Then: raw deletion is not attempted and the same item remains retryable.
    assert result.state == "cleanup_pending"
    assert saver.deleted_threads == []
    stored = await db.get(AgentPurgeCleanupItem, item.id)
    assert stored is not None and stored.status == "failed"


async def test_unproven_file_locator_fails_without_removing_bytes(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a forged cleanup item points outside the proven Agent config root.
    target = write_outside_file(tmp_path)
    receipt, item = await seed_cleanup_receipt(db, "agent_files", str(target))

    # When: cleanup validates the persisted locator.
    result = await process_agent_purge_cleanup(db, receipt.id)
    await db.commit()

    # Then: the item remains pending and unrelated bytes are unchanged.
    assert result.state == "cleanup_pending"
    assert target.read_text(encoding="utf-8") == "conversation-owned bytes"
    stored = await db.get(AgentPurgeCleanupItem, item.id)
    assert stored is not None and stored.status == "failed"


async def test_symlink_escape_fails_without_removing_conversation_bytes(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the canonical Agent directory is a symlink to conversation-owned storage.
    receipt, item = await seed_cleanup_receipt(db, "agent_files", "placeholder")
    data_root = tmp_path / "data"
    outside = tmp_path / "conversation-owned"
    target = outside / "artifact.bin"
    link = data_root / "agents" / str(receipt.agent_id_snapshot)
    await AsyncPath(outside).mkdir(parents=True)
    await AsyncPath(target).write_bytes(b"must survive")
    await AsyncPath(link.parent).mkdir(parents=True)
    await AsyncPath(link).symlink_to(outside, target_is_directory=True)
    locator = str(link.absolute())
    item.locator = locator
    item.locator_sha256 = sha256(locator.encode()).hexdigest()
    await db.commit()
    monkeypatch.setattr(agent_purge_cleanup, "runtime_data_dir", lambda: data_root)

    # When: cleanup resolves the persisted locator before deletion.
    result = await process_agent_purge_cleanup(db, receipt.id)
    await db.commit()

    # Then: the symlink item remains retryable and external bytes survive unchanged.
    assert result.state == "cleanup_pending"
    assert await AsyncPath(target).read_bytes() == b"must survive"
    assert await AsyncPath(link).is_symlink()
