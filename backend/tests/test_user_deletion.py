"""User-row deletion and database cascade contracts."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.credential import Credential
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.user_service import delete_user
from tests.test_user_cleanup import _make_agent, _make_refresh_token, _make_user


@pytest.mark.asyncio
async def test_delete_user_cascades_to_agent(db: AsyncSession) -> None:
    user = await _make_user(db)
    agent = await _make_agent(db, user.id)

    with patch("app.agent_runtime.checkpointer.delete_thread", AsyncMock()):
        await delete_user(db, user.id)
    await db.commit()

    assert (await db.execute(select(User).where(User.id == user.id))).scalar_one_or_none() is None
    assert (
        await db.execute(select(Agent).where(Agent.id == agent.id))
    ).scalar_one_or_none() is None


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
