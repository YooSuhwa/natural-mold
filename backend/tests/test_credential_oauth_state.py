from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential_oauth_state import CredentialOAuthState
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


@pytest.mark.asyncio
async def test_credential_oauth_state_can_be_created_and_consumed(db: AsyncSession) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={"server_url": "https://mcp.atlassian.com/v1/mcp/authv2"},
    )
    state = CredentialOAuthState(
        state_hash="hash-1",
        credential_id=cred.id,
        user_id=TEST_USER_ID,
        redirect_uri="http://localhost:8001/api/oauth2-credential/callback",
        code_verifier="verifier",
        origin="credential",
        metadata_json={"provider": "atlassian"},
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
    )
    db.add(state)
    await db.commit()

    row = (
        await db.execute(
            select(CredentialOAuthState).where(CredentialOAuthState.state_hash == "hash-1")
        )
    ).scalar_one()
    assert row.consumed_at is None

    row.consumed_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()

    consumed = (
        await db.execute(
            select(CredentialOAuthState).where(CredentialOAuthState.state_hash == "hash-1")
        )
    ).scalar_one()
    assert consumed.consumed_at is not None
