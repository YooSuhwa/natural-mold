"""User-row deletion and database cascade contracts."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.message_attachment import MessageAttachment
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.user_service import delete_user, remove_user_files
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.test_user_cleanup import (
    _make_agent,
    _make_conversation,
    _make_refresh_token,
    _make_user,
)


@pytest.mark.asyncio
async def test_delete_user_requires_resource_transfer_before_cleanup(db: AsyncSession) -> None:
    user = await _make_user(db)
    agent = await _make_agent(db, user.id)
    checkpoint = AsyncMock()
    with (
        patch("app.agent_runtime.checkpointer.delete_thread", checkpoint),
        pytest.raises(AppError) as captured,
    ):
        await delete_user(db, user.id)
    assert captured.value.code == "resource_transfer_required"
    assert captured.value.status == 409
    assert await db.get(User, user.id) is not None
    assert await db.get(Agent, agent.id) is not None
    checkpoint.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_user_noop_for_unknown_id(db: AsyncSession) -> None:
    with patch("app.agent_runtime.checkpointer.delete_thread", AsyncMock()):
        await delete_user(db, uuid.uuid4())


@pytest.mark.asyncio
async def test_delete_user_does_not_remove_system_credentials(db: AsyncSession) -> None:
    user = await _make_user(db, email="del@test.com")
    system_credential = Credential(
        id=uuid.uuid4(),
        user_id=None,
        definition_key="anthropic",
        name="op anthropic",
        data_encrypted="opaque",
        key_id="kv1",
        is_system=True,
        org_id=None,
        tenant_id=None,
    )
    db.add(system_credential)
    await db.flush()

    with patch("app.agent_runtime.checkpointer.delete_thread", AsyncMock()):
        await delete_user(db, user.id)
    await db.commit()

    surviving = (
        await db.execute(select(Credential).where(Credential.id == system_credential.id))
    ).scalar_one_or_none()
    assert surviving is not None
    assert surviving.is_system is True
    assert surviving.user_id is None


@pytest.mark.asyncio
async def test_delete_user_cascades_refresh_tokens(db: AsyncSession) -> None:
    user = await _make_user(db, email="rt-cascade@test.com")
    token = await _make_refresh_token(db, user.id)

    with patch("app.agent_runtime.checkpointer.delete_thread", AsyncMock()):
        await delete_user(db, user.id)
    await db.commit()

    rows = (
        (await db.execute(select(RefreshToken).where(RefreshToken.id == token.id))).scalars().all()
    )
    assert rows == []


def _attachment(
    user_id: uuid.UUID, conversation_id: uuid.UUID | None, path: Path
) -> MessageAttachment:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return MessageAttachment(
        user_id=user_id,
        conversation_id=conversation_id,
        filename=path.name,
        mime_type="image/png",
        size_bytes=1,
        storage_path=str(path),
        url="/api/uploads/x",
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
    )


@pytest.mark.asyncio
async def test_delete_user_removes_the_accounts_files_after_commit(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.agent_runtime import runtime_config
    from app.config import settings

    monkeypatch.setattr(runtime_config, "_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    user = await _make_user(db, email="files@test.com")
    other = await _make_user(db, email="keeper@test.com")
    agent = await _make_agent(db, other.id)  # the account owns no resources
    conversation = await _make_conversation(db, agent.id)
    conversation.user_id = user.id
    kept_conversation = await _make_conversation(db, agent.id)
    uploads = tmp_path / "uploads"
    trees = tmp_path / "artifacts" / "tenants" / str(TEST_TENANT_ID) / "conversations"
    owned = [uploads / "sent.png", uploads / "unsent.png", trees / str(conversation.id) / "a.md"]
    kept = [uploads / "other.png", trees / str(kept_conversation.id) / "b.md"]
    db.add_all(
        [
            _attachment(user.id, conversation.id, owned[0]),
            _attachment(user.id, None, owned[1]),
            _attachment(other.id, kept_conversation.id, kept[0]),
        ]
    )
    owned[2].parent.mkdir(parents=True)
    owned[2].write_text("x")
    kept[1].parent.mkdir(parents=True)
    kept[1].write_text("x")
    await db.flush()

    with patch("app.agent_runtime.checkpointer.delete_thread", AsyncMock()):
        deleted = await delete_user(db, user.id)
    assert all(path.exists() for path in owned)  # nothing is removed before the commit
    await db.commit()
    await remove_user_files(deleted)

    assert [path for path in owned if path.exists()] == []
    assert not owned[2].parent.exists()
    assert all(path.exists() for path in kept)
