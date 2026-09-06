"""Tests for ``app.services.user_service.cleanup_user_resources`` + ``delete_user``.

ADR-016 §6 / Phase 6-C — verifies that:

1. LangGraph checkpoint deletion is invoked once per owned conversation.
2. Active refresh tokens are revoked.
3. Deleting the user cascades to their agents (via FK CASCADE in the schema).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadStorage
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.user_service import cleanup_user_resources


@pytest.fixture(autouse=True)
def _isolate_offload_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.agent_runtime import runtime_config

    monkeypatch.setattr(runtime_config, "_DATA_DIR", tmp_path)


async def _make_user(db: AsyncSession, *, email: str = "u1@test.com") -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        name="U",
        hashed_password="h",
        is_active=True,
        is_super_user=False,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_model(db: AsyncSession) -> Model:
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
    )
    db.add(model)
    await db.flush()
    return model


async def _make_agent(db: AsyncSession, user_id: uuid.UUID) -> Agent:
    model = await _make_model(db)
    agent = Agent(
        id=uuid.uuid4(),
        user_id=user_id,
        name="A",
        system_prompt="hi",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    return agent


async def _make_conversation(db: AsyncSession, agent_id: uuid.UUID) -> Conversation:
    conv = Conversation(id=uuid.uuid4(), agent_id=agent_id, title="t")
    db.add(conv)
    await db.flush()
    return conv


async def _make_refresh_token(db: AsyncSession, user_id: uuid.UUID) -> RefreshToken:
    tok = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=uuid.uuid4().hex,
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )
    db.add(tok)
    await db.flush()
    return tok


def _write_conversation_offloads(
    data_root: Path,
    *,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> None:
    backend = ScopedOffloadStorage(
        data_dir=data_root,
        identity=OffloadIdentity(
            owner_id=str(owner_id),
            conversation_id=str(conversation_id),
            run_id="cleanup-test-run",
        ),
    ).for_actor(uuid.UUID("11111111-1111-4111-8111-111111111111"))
    history = (
        f"{backend.artifacts_root}/conversation_history/session_0123456789abcdef0123456789abcdef.md"
    )
    spill = f"{backend.artifacts_root}/large_tool_results/cleanup-test"
    assert backend.write(history, "history").error is None
    assert backend.write(spill, "spill").error is None


@pytest.mark.asyncio
async def test_cleanup_deletes_threads_for_each_conversation(db: AsyncSession):
    """Each conversation owned (via Agent.user_id) → one ``delete_thread`` call."""

    user = await _make_user(db)
    agent = await _make_agent(db, user.id)
    conv1 = await _make_conversation(db, agent.id)
    conv2 = await _make_conversation(db, agent.id)

    delete_thread_mock = AsyncMock()
    with patch("app.agent_runtime.checkpointer.delete_thread", delete_thread_mock):
        await cleanup_user_resources(db, user.id)

    called_with = sorted(c.args[0] for c in delete_thread_mock.call_args_list)
    assert called_with == sorted([str(conv1.id), str(conv2.id)])


@pytest.mark.asyncio
async def test_cleanup_skips_other_users_conversations(db: AsyncSession):
    """Only the target user's threads are touched."""

    target = await _make_user(db, email="target@test.com")
    other = await _make_user(db, email="other@test.com")
    target_agent = await _make_agent(db, target.id)
    other_agent = await _make_agent(db, other.id)
    target_conv = await _make_conversation(db, target_agent.id)
    other_conv = await _make_conversation(db, other_agent.id)

    delete_thread_mock = AsyncMock()
    with patch("app.agent_runtime.checkpointer.delete_thread", delete_thread_mock):
        await cleanup_user_resources(db, target.id)

    called = [c.args[0] for c in delete_thread_mock.call_args_list]
    assert str(target_conv.id) in called
    assert str(other_conv.id) not in called


@pytest.mark.asyncio
async def test_cleanup_revokes_active_refresh_tokens(db: AsyncSession):
    """All active refresh tokens for the user → ``revoked_at`` populated."""

    user = await _make_user(db)
    active = await _make_refresh_token(db, user.id)
    already_revoked = await _make_refresh_token(db, user.id)
    already_revoked.revoked_at = datetime.now(UTC) - timedelta(days=1)
    await db.flush()
    revoked_at_before = already_revoked.revoked_at
    assert revoked_at_before is not None

    with patch("app.agent_runtime.checkpointer.delete_thread", AsyncMock()):
        await cleanup_user_resources(db, user.id)

    await db.refresh(active)
    await db.refresh(already_revoked)
    assert active.revoked_at is not None
    # Pre-revoked tokens keep their original ``revoked_at`` (the WHERE clause
    # filtered them out), preserving forensic timestamps. SQLite drops the
    # tzinfo on round-trip so compare naive timestamps.
    already_revoked_at = already_revoked.revoked_at
    assert already_revoked_at is not None
    assert already_revoked_at.replace(tzinfo=None) == revoked_at_before.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_cleanup_handles_checkpointer_unavailable(db: AsyncSession):
    """Checkpointer raise → cleanup logs and continues (refresh tokens still revoked)."""

    user = await _make_user(db)
    agent = await _make_agent(db, user.id)
    await _make_conversation(db, agent.id)
    await _make_refresh_token(db, user.id)

    failing = AsyncMock(side_effect=RuntimeError("not initialised"))
    with patch("app.agent_runtime.checkpointer.delete_thread", failing):
        # Should NOT raise — cleanup must keep going so the user delete
        # path doesn't get stuck on a transient checkpoint store outage.
        await cleanup_user_resources(db, user.id)

    rows = (
        (await db.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert all(r.revoked_at is not None for r in rows)


@pytest.mark.asyncio
async def test_cleanup_rejects_active_run_before_mutating_external_or_owner_state(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active work makes user cleanup fail closed before any side effect."""

    user = await _make_user(db)
    agent = await _make_agent(db, user.id)
    conversation = await _make_conversation(db, agent.id)
    token = await _make_refresh_token(db, user.id)
    db.add(
        ConversationRun(
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=user.id,
            source="chat",
            status="running",
            is_active=True,
        )
    )
    await db.flush()

    from app.agent_runtime import checkpointer, offload_storage

    delete_thread = AsyncMock()
    delete_offloads = AsyncMock()
    monkeypatch.setattr(checkpointer, "delete_thread", delete_thread)
    monkeypatch.setattr(offload_storage, "delete_conversation_offloads", delete_offloads)

    with pytest.raises(RuntimeError, match="active conversation run"):
        await cleanup_user_resources(db, user.id)

    delete_thread.assert_not_awaited()
    delete_offloads.assert_not_awaited()
    await db.refresh(token)
    assert token.revoked_at is None
    assert await db.get(User, user.id) is not None
    assert await db.get(Conversation, conversation.id) is not None
