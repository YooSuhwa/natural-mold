"""Orphan attachment GC (data hygiene + storage cleanup).

``POST /api/uploads`` creates a ``message_attachments`` row with
``message_id IS NULL``; turn finalize stamps the message id once the upload is
actually sent (M1). A row still NULL past the retention window was staged in
the composer but never sent — invisible to every read path and never cleaned
up. This locks in the contract: only never-sent uploads that are BOTH old AND
``message_id IS NULL`` are collected (row + on-disk blob); sent uploads and
recent staged ones stay, files intact.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_attachment import MessageAttachment
from app.models.user import User
from app.services.chat_service import gc_orphan_attachments


def _exists(path: Path) -> bool:
    """Sync filesystem probe (async tests avoid pathlib I/O — ASYNC240)."""

    return path.is_file()


async def _seed_user(db: AsyncSession) -> uuid.UUID:
    user = User(id=uuid.uuid4(), email=f"u-{uuid.uuid4().hex[:8]}@test.com", name="Test")
    db.add(user)
    await db.flush()
    return user.id


def _attach(
    user_id: uuid.UUID,
    *,
    storage_dir: Path,
    message_id: str | None,
    age_hours: float,
) -> MessageAttachment:
    created = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=age_hours)
    upload_id = uuid.uuid4()
    storage_path = storage_dir / f"{upload_id}.txt"
    storage_path.write_bytes(b"payload")
    return MessageAttachment(
        id=upload_id,
        user_id=user_id,
        conversation_id=None,
        message_id=message_id,
        filename="f.txt",
        mime_type="text/plain",
        size_bytes=7,
        storage_path=str(storage_path),
        url=f"/api/uploads/{upload_id}",
        created_at=created,
    )


@pytest.mark.asyncio
async def test_gc_deletes_only_old_unsent_uploads(db: AsyncSession, tmp_path: Path) -> None:
    user_id = await _seed_user(db)
    storage_dir = tmp_path / "uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)

    # Old + never sent — the only one collected (row + file).
    orphan = _attach(user_id, storage_dir=storage_dir, message_id=None, age_hours=48)
    # Recently staged (inside the 24h window) — user may still be composing.
    recent = _attach(user_id, storage_dir=storage_dir, message_id=None, age_hours=1)
    # Old but SENT (message_id stamped at finalize) — must stay.
    sent = _attach(user_id, storage_dir=storage_dir, message_id="some-msg-id", age_hours=48)
    db.add_all([orphan, recent, sent])
    await db.commit()

    orphan_id, orphan_file = orphan.id, Path(orphan.storage_path)
    survivor_ids = {recent.id, sent.id}
    survivor_files = [Path(recent.storage_path), Path(sent.storage_path)]

    deleted = await gc_orphan_attachments(db, retention_hours=24)
    assert deleted == 1

    remaining = {row.id for row in (await db.execute(select(MessageAttachment))).scalars().all()}
    assert orphan_id not in remaining
    assert survivor_ids <= remaining

    # Blob of the collected orphan is gone; survivors' blobs untouched.
    assert not _exists(orphan_file)
    assert all(_exists(f) for f in survivor_files)


@pytest.mark.asyncio
async def test_gc_returns_zero_when_nothing_to_collect(db: AsyncSession, tmp_path: Path) -> None:
    user_id = await _seed_user(db)
    storage_dir = tmp_path / "uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)
    db.add(_attach(user_id, storage_dir=storage_dir, message_id=None, age_hours=1))
    db.add(_attach(user_id, storage_dir=storage_dir, message_id="sent", age_hours=48))
    await db.commit()

    assert await gc_orphan_attachments(db, retention_hours=24) == 0


@pytest.mark.asyncio
async def test_gc_negative_retention_rejected(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="retention_hours must be >= 1"):
        await gc_orphan_attachments(db, retention_hours=-1)


@pytest.mark.asyncio
async def test_gc_zero_retention_rejected_and_spares_just_staged_upload(
    db: AsyncSession, tmp_path: Path
) -> None:
    # ``retention_hours == 0`` sets ``cutoff = now``, so an upload staged moments
    # ago (still composing) would be deleted. We REJECT 0 (chosen over clamping
    # so a mis-set value surfaces loudly); the fresh upload must survive untouched.
    user_id = await _seed_user(db)
    storage_dir = tmp_path / "uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)
    fresh = _attach(user_id, storage_dir=storage_dir, message_id=None, age_hours=0)
    db.add(fresh)
    await db.commit()
    fresh_id, fresh_file = fresh.id, Path(fresh.storage_path)

    with pytest.raises(ValueError, match="retention_hours must be >= 1"):
        await gc_orphan_attachments(db, retention_hours=0)

    remaining = {row.id for row in (await db.execute(select(MessageAttachment))).scalars().all()}
    assert fresh_id in remaining
    assert _exists(fresh_file)
