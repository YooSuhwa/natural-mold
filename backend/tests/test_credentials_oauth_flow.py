from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.registry import registry
from app.models.credential_audit_log import CredentialAuditLog
from app.models.credential_oauth_state import CredentialOAuthState
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User", is_super_user=True))
        await db.commit()


@pytest.mark.asyncio
async def test_oauth_start_creates_persistent_state(
    db: AsyncSession,
    client: AsyncClient,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
            "use_dynamic_client_registration": False,
            "auth_url": "https://id.example/authorize",
            "access_token_url": "https://id.example/token",
            "client_id": "cid",
            "scope": "read:jira",
            "grant_type": "pkce",
            "authentication": "none",
        },
    )
    await db.commit()

    response = await client.post(f"/api/oauth2-credential/auth/{cred.id}")

    assert response.status_code == 200
    url = response.json()["authorization_url"]
    assert "https://id.example/authorize" in url
    assert "code_challenge=" in url

    rows = (await db.execute(select(CredentialOAuthState))).scalars().all()
    assert len(rows) == 1
    assert rows[0].credential_id == cred.id
    assert rows[0].code_verifier


@pytest.mark.asyncio
async def test_oauth_start_audits_persisted_credential_payload(
    db: AsyncSession,
    client: AsyncClient,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
            "use_dynamic_client_registration": False,
            "auth_url": "https://id.example/authorize",
            "access_token_url": "https://id.example/token",
            "client_id": "cid",
            "grant_type": "pkce",
            "authentication": "none",
        },
    )
    await db.commit()

    response = await client.post(f"/api/oauth2-credential/auth/{cred.id}")

    assert response.status_code == 200
    logs = (
        (
            await db.execute(
                select(CredentialAuditLog)
                .where(
                    CredentialAuditLog.credential_id == cred.id,
                    CredentialAuditLog.action == "update",
                )
                .order_by(CredentialAuditLog.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    assert logs
    assert logs[0].log_metadata == {
        "data_changed": True,
        "trigger": "oauth_start",
    }


@pytest.mark.asyncio
async def test_oauth_start_cleans_expired_and_consumed_states(
    db: AsyncSession,
    client: AsyncClient,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
            "use_dynamic_client_registration": False,
            "auth_url": "https://id.example/authorize",
            "access_token_url": "https://id.example/token",
            "client_id": "cid",
            "grant_type": "pkce",
            "authentication": "none",
        },
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    db.add_all(
        [
            CredentialOAuthState(
                state_hash="expired-state",
                credential_id=cred.id,
                user_id=TEST_USER_ID,
                redirect_uri="http://test/api/oauth2-credential/callback",
                origin="credential",
                expires_at=now - timedelta(minutes=1),
            ),
            CredentialOAuthState(
                state_hash="consumed-state",
                credential_id=cred.id,
                user_id=TEST_USER_ID,
                redirect_uri="http://test/api/oauth2-credential/callback",
                origin="credential",
                consumed_at=now,
                expires_at=now + timedelta(minutes=10),
            ),
        ]
    )
    await db.commit()

    response = await client.post(f"/api/oauth2-credential/auth/{cred.id}")

    assert response.status_code == 200
    state_hashes = {
        row.state_hash for row in (await db.execute(select(CredentialOAuthState))).scalars().all()
    }
    assert "expired-state" not in state_hashes
    assert "consumed-state" not in state_hashes
    assert len(state_hashes) == 1


@pytest.mark.asyncio
async def test_oauth_callback_consumes_state_and_stores_token(
    db: AsyncSession,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "access_token_url": "https://id.example/token",
            "client_id": "cid",
            "authentication": "none",
        },
    )
    raw_state = "state-raw"
    db.add(
        CredentialOAuthState(
            state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
            credential_id=cred.id,
            user_id=TEST_USER_ID,
            redirect_uri="http://test/api/oauth2-credential/callback",
            code_verifier="verifier",
            origin="credential",
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
        )
    )
    await db.commit()

    async def fake_pre_auth(data: dict[str, Any]) -> dict[str, Any]:
        assert data["authorization_code"] == "code-1"
        assert data["code_verifier"] == "verifier"
        return {
            "access_token": "fresh",
            "refresh_token": "refresh",
            "expires_at": 9999999999.0,
            "token_type": "Bearer",
        }

    definition = registry.require("mcp_oauth2")
    monkeypatch.setattr(definition, "pre_authentication", fake_pre_auth)

    response = await client.get(
        "/api/oauth2-credential/callback",
        params={"code": "code-1", "state": raw_state},
    )

    assert response.status_code == 200
    assert "OAuth authorization completed" in response.text
    await db.refresh(cred)
    payload = credential_service.decrypt_data(cred.data_encrypted)
    assert payload["access_token"] == "fresh"
    assert payload["refresh_token"] == "refresh"
    assert payload.get("code_verifier") is None
    state_row = (await db.execute(select(CredentialOAuthState))).scalar_one()
    assert state_row.consumed_at is not None


@pytest.mark.asyncio
async def test_update_preserves_oauth_tokens_for_mcp_oauth2(db: AsyncSession) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 9999999999.0,
            "client_id": "cid",
        },
    )
    await db.commit()

    await credential_service.update(
        db,
        credential=cred,
        actor_user_id=TEST_USER_ID,
        data={"server_url": "https://mcp.atlassian.com/v1/mcp/authv2"},
    )
    await db.commit()
    await db.refresh(cred)

    payload = credential_service.decrypt_data(cred.data_encrypted)
    assert payload["access_token"] == "access"
    assert payload["refresh_token"] == "refresh"
    assert payload["expires_at"] == 9999999999.0
    assert payload["client_id"] == "cid"


@pytest.mark.asyncio
async def test_update_drops_oauth_session_when_mcp_server_url_changes(
    db: AsyncSession,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
            "auth_url": "https://id.atlassian.com/authorize",
            "access_token_url": "https://id.atlassian.com/token",
            "client_id": "old-client",
            "client_secret": "old-secret",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": 9999999999.0,
        },
    )
    await db.commit()

    await credential_service.update(
        db,
        credential=cred,
        actor_user_id=TEST_USER_ID,
        data={"server_url": "https://mcp.new.example/v1/mcp"},
    )
    await db.commit()
    await db.refresh(cred)

    payload = credential_service.decrypt_data(cred.data_encrypted)
    assert payload["server_url"] == "https://mcp.new.example/v1/mcp"
    assert "client_id" not in payload
    assert "client_secret" not in payload
    assert "access_token" not in payload
    assert "refresh_token" not in payload
    assert "expires_at" not in payload
