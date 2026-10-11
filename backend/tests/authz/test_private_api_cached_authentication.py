"""Independent cached API authentication boundary witness."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.agent_api import dependencies, service
from app.dependencies import get_db
from app.exceptions import AppError
from app.models.agent_api import AgentApiKey

if TYPE_CHECKING:
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("revoked", [False, True])
async def test_cached_authentication_uses_current_key_state(
    db: AsyncSession, api_case: ApiCase, revoked: bool
) -> None:
    key = api_case.key
    if revoked:
        await db.execute(
            update(AgentApiKey)
            .where(AgentApiKey.id == key.id)
            .values(revoked_at=service.utc_now_naive())
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        assert key.revoked_at is None
    request = Request(
        {"type": "http", "headers": [(b"authorization", f"Bearer {api_case.cleartext}".encode())]}
    )
    denied = False
    try:
        await dependencies.get_api_key_principal(request, db)
    except AppError as error:
        denied = error.status == 401
    await db.refresh(key)
    assert denied == revoked
    assert key.usage_count == (0 if revoked else 1)


@pytest.mark.parametrize("preloaded", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
async def test_http_key_rechecks_current_state(
    db: AsyncSession,
    api_case: ApiCase,
    raw_test_app: FastAPI,
    preloaded: bool,
    revoked: bool,
) -> None:
    key = api_case.key
    if revoked:
        await db.execute(
            update(AgentApiKey)
            .where(AgentApiKey.id == key.id)
            .values(revoked_at=service.utc_now_naive())
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        assert key.revoked_at is None
    original = raw_test_app.dependency_overrides[get_db]

    async def cached_session() -> AsyncGenerator[AsyncSession, None]:
        yield db

    if preloaded:
        raw_test_app.dependency_overrides[get_db] = cached_session
    try:
        response = await api_case.client.get(
            "/v1/agents", headers={"Authorization": f"Bearer {api_case.cleartext}"}
        )
    finally:
        raw_test_app.dependency_overrides[get_db] = original
    await db.refresh(key)
    assert response.status_code == (401 if revoked else 200)
    assert key.usage_count == (0 if revoked else 1)
