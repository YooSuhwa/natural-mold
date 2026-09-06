"""User-deletion coverage for scoped internal offload storage."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadStorage
from app.models.user import User
from app.services.user_service import cleanup_user_resources, delete_user
from tests.test_user_cleanup import (
    _make_agent,
    _make_conversation,
    _make_user,
    _write_conversation_offloads,
)


@pytest.mark.asyncio
async def test_cleanup_deletes_owned_offloads_and_fences_late_constructor(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = await _make_user(db)
    agent = await _make_agent(db, user.id)
    first = await _make_conversation(db, agent.id)
    second = await _make_conversation(db, agent.id)
    _write_conversation_offloads(tmp_path, owner_id=user.id, conversation_id=first.id)
    _write_conversation_offloads(tmp_path, owner_id=user.id, conversation_id=second.id)
    artifact = tmp_path / "artifacts" / "sentinel"
    artifact.parent.mkdir()
    artifact.write_text("keep")
    from app.agent_runtime import checkpointer, offload_storage, runtime_config

    monkeypatch.setattr(runtime_config, "_DATA_DIR", tmp_path)
    events: list[tuple[str, str]] = []
    blocked: list[str] = []
    real_delete_offloads = offload_storage.delete_conversation_offloads

    async def record_checkpoint(thread_id: str) -> None:
        events.append(("checkpoint", thread_id))

    def record_offload(
        data_dir: Path, *, owner_id: str, conversation_id: str
    ) -> offload_storage.OffloadCleanupReceipt:
        events.append(("offload", conversation_id))
        receipt = real_delete_offloads(
            data_dir,
            owner_id=owner_id,
            conversation_id=conversation_id,
        )
        with pytest.raises(offload_storage.OffloadSecurityError, match="deleted"):
            ScopedOffloadStorage(
                data_dir=data_dir,
                identity=OffloadIdentity(
                    owner_id=owner_id,
                    conversation_id=conversation_id,
                    run_id="late-user-delete-run",
                ),
            ).for_actor(uuid.UUID("22222222-2222-4222-8222-222222222222"))
        blocked.append(conversation_id)
        return receipt

    monkeypatch.setattr(checkpointer, "delete_thread", record_checkpoint)
    monkeypatch.setattr(offload_storage, "delete_conversation_offloads", record_offload)

    await cleanup_user_resources(db, user.id)

    expected_ids = {str(first.id), str(second.id)}
    assert len(events) == 4
    for index in range(0, len(events), 2):
        checkpoint, offload = events[index : index + 2]
        assert checkpoint[0] == "checkpoint"
        assert offload == ("offload", checkpoint[1])
    assert {value for _, value in events} == expected_ids
    assert set(blocked) == expected_ids
    assert not any(path.is_file() for path in (tmp_path / ".moldy-internal" / "offload").rglob("*"))
    assert artifact.read_text() == "keep"

    await cleanup_user_resources(db, user.id)
    assert not any(path.is_file() for path in (tmp_path / ".moldy-internal" / "offload").rglob("*"))


@pytest.mark.asyncio
async def test_cleanup_continues_when_one_conversation_cleanup_fails(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = await _make_user(db)
    agent = await _make_agent(db, user.id)
    failing = await _make_conversation(db, agent.id)
    sibling = await _make_conversation(db, agent.id)
    _write_conversation_offloads(tmp_path, owner_id=user.id, conversation_id=failing.id)
    _write_conversation_offloads(tmp_path, owner_id=user.id, conversation_id=sibling.id)
    from app.agent_runtime import checkpointer, offload_storage, runtime_config

    monkeypatch.setattr(runtime_config, "_DATA_DIR", tmp_path)
    checkpointed: list[str] = []
    offloaded: list[str] = []
    real_delete_offloads = offload_storage.delete_conversation_offloads

    async def record_checkpoint(thread_id: str) -> None:
        checkpointed.append(thread_id)

    def record_offload(
        data_dir: Path, *, owner_id: str, conversation_id: str
    ) -> offload_storage.OffloadCleanupReceipt:
        offloaded.append(conversation_id)
        if conversation_id == str(failing.id):
            raise PermissionError("injected cleanup failure")
        return real_delete_offloads(
            data_dir,
            owner_id=owner_id,
            conversation_id=conversation_id,
        )

    monkeypatch.setattr(checkpointer, "delete_thread", record_checkpoint)
    monkeypatch.setattr(offload_storage, "delete_conversation_offloads", record_offload)

    with pytest.raises(RuntimeError, match="offload cleanup incomplete"):
        await cleanup_user_resources(db, user.id)

    expected_ids = {str(failing.id), str(sibling.id)}
    assert set(checkpointed) == expected_ids
    assert set(offloaded) == expected_ids


@pytest.mark.asyncio
async def test_delete_user_keeps_owner_rows_when_offload_cleanup_fails(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _make_user(db)
    agent = await _make_agent(db, user.id)
    await _make_conversation(db, agent.id)
    from app.agent_runtime import checkpointer, offload_storage

    monkeypatch.setattr(checkpointer, "delete_thread", AsyncMock())

    def fail_offload_cleanup(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("injected cleanup failure")

    monkeypatch.setattr(offload_storage, "delete_conversation_offloads", fail_offload_cleanup)

    with pytest.raises(RuntimeError, match="offload cleanup incomplete"):
        await delete_user(db, user.id)

    assert (
        await db.execute(select(User).where(User.id == user.id))
    ).scalar_one_or_none() is not None
