from __future__ import annotations

from pathlib import Path

import pytest
from httpx import Response
from sqlalchemy import select, text

from app.config import settings
from app.models.user import User
from tests.authz.agentless_history_app import agentless_history_app
from tests.authz.agentless_history_fixture import (
    assert_history_surfaces,
    read_history_surfaces,
    seed_native_history,
)
from tests.authz.native_company_selection import select_second_company
from tests.authz.organization_migration_fixture import alembic_head

pytestmark = pytest.mark.integration


class NativeRegistrationHTTP403Error(AssertionError):
    """The real registration policy or organization scope denied signup."""


class NativeRegistrationHTTP409Error(AssertionError):
    """The supposedly fresh native lab already contains the registration identity."""


class NativeRegistrationHTTP422Error(AssertionError):
    """The real registration request failed schema validation."""


class NativeRegistrationHTTP429Error(AssertionError):
    """The native lane unexpectedly applied the public registration rate limit."""


class NativeRegistrationHTTP5xxError(AssertionError):
    """The real registration path failed inside the application or database."""


class NativeRegistrationUnexpectedStatusError(AssertionError):
    """The real registration path returned another non-success status."""


def require_native_registration(response: Response) -> None:
    """Expose a secret-free status class when the canonical runner strips response text."""
    if response.status_code == 201:
        return
    if response.status_code == 403:
        raise NativeRegistrationHTTP403Error
    if response.status_code == 409:
        raise NativeRegistrationHTTP409Error
    if response.status_code == 422:
        raise NativeRegistrationHTTP422Error
    if response.status_code == 429:
        raise NativeRegistrationHTTP429Error
    if response.status_code >= 500:
        raise NativeRegistrationHTTP5xxError
    raise NativeRegistrationUnexpectedStatusError


async def test_agentless_history_uses_real_jwt_scope_and_rejects_continuation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: a real JWT owner session and complete purged Conversation history.
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path))
    async with agentless_history_app(monkeypatch) as stack:
        async with stack.sessions() as db:
            # The deployable tip includes B11 while retaining the B08 lifecycle schema.
            schema_state = (
                await db.execute(
                    text(
                        "SELECT (SELECT version_num FROM alembic_version), "
                        "to_regclass('conversation_history_manifests')::text, "
                        "EXISTS (SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='conversations' "
                        "AND column_name='agent_lifecycle_state')"
                    )
                )
            ).one()
            assert schema_state == (
                alembic_head(),
                "conversation_history_manifests",
                True,
            )
        owner = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "history-owner@example.com",
                "name": "History owner",
                "password": "history-owner-password",
            },
        )
        require_native_registration(owner)
        csrf = owner.json()["csrf_token"]
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "history-owner@example.com"))
            assert user is not None
            seed = await seed_native_history(db, user, tmp_path)

        deployment = await stack.client.post(
            "/api/agent-api/deployments",
            headers={"X-CSRF-Token": csrf},
            json={"agent_id": str(seed.api_agent_id)},
        )
        assert deployment.status_code == 201, deployment.text
        key = await stack.client.post(
            "/api/agent-api/keys",
            headers={"X-CSRF-Token": csrf},
            json={
                "name": "History boundary key",
                "scopes": ["read"],
                "allow_all_deployments": False,
                "deployment_ids": [deployment.json()["id"]],
            },
        )
        assert key.status_code == 201, key.text
        public_api_key = key.json()["key"]
        surfaces = await read_history_surfaces(stack.client, seed)

        message_mutation = await stack.client.post(
            f"/api/conversations/{seed.conversation_id}/messages",
            headers={"X-CSRF-Token": csrf},
            json={"content": "must not run"},
        )
        state_mutation = await stack.client.post(
            f"/api/conversations/{seed.conversation_id}/langgraph/threads/"
            f"{seed.conversation_id}/state",
            headers={"X-CSRF-Token": csrf},
            json={"values": {"messages": []}},
        )

        # When: the same JWT actor selects a different company and organization.
        await select_second_company(
            stack,
            {"X-CSRF-Token": csrf},
            "history-owner@example.com",
        )
        foreign_company_read = await stack.client.get(
            f"/api/conversations/{seed.conversation_id}/history"
        )

        # When: a second real JWT session probes the first owner's identifier.
        foreign = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "history-foreign@example.com",
                "name": "History foreign",
                "password": "history-foreign-password",
            },
        )
        assert foreign.status_code == 201, foreign.text
        foreign_read = await stack.client.get(f"/api/conversations/{seed.conversation_id}/history")
        stack.client.cookies.clear()
        api_key_read = await stack.client.get(
            f"/api/conversations/{seed.conversation_id}/history",
            headers={"X-API-Key": public_api_key},
        )

    # Then: owner reads are complete and exact, while every continuation is fenced.
    assert_history_surfaces(surfaces)
    assert message_mutation.status_code == 409, message_mutation.text
    assert state_mutation.status_code == 409, state_mutation.text
    assert foreign_company_read.status_code == 404, foreign_company_read.text
    assert foreign_read.status_code == 404, foreign_read.text
    assert api_key_read.status_code == 401, api_key_read.text
