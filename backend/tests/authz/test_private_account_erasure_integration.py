"""Actual m83 account deletion rejects owned resources before external cleanup."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.config import settings
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.model import Model
from app.models.user import User
from app.services.user_service import delete_user
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_native_account_erasure_requires_releasing_agent_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-erasure@example.com",
                "name": "Erasure fixture",
                "password": "native-erasure-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        created = await stack.client.post(
            "/api/agents",
            headers=headers,
            json={"name": "Erasure fixture", "system_prompt": "Fixture", "model_id": str(model_id)},
        )
        assert created.status_code == 201, created.text
        checkpoint = AsyncMock()
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-erasure@example.com"))
            assert user is not None
            with (
                patch("app.agent_runtime.checkpointer.delete_thread", checkpoint),
                pytest.raises(AppError) as captured,
            ):
                await delete_user(db, user.id)
            assert (
                captured.value.code == "resource_transfer_required" and captured.value.status == 409
            )
            await db.commit()
            assert await db.get(User, user.id) is not None
            assert await db.scalar(select(Agent.id)) is not None
        checkpoint.assert_not_awaited()
