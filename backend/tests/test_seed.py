"""bootstrap_credentials_from_env — env → mock_user Credential seed."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.user import User
from app.seed.bootstrap_from_env import (
    SEED_NAME_PREFIX,
    bootstrap_credentials_from_env,
)
from tests.conftest import TEST_USER_ID


async def _ensure_user(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none():
        return
    db.add(User(id=TEST_USER_ID, email="seed@test", name="seed"))
    await db.commit()


class TestBootstrap:
    @pytest.mark.asyncio
    async def test_creates_openai_credential_from_env(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _ensure_user(db)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        for var in (
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_CSE_ID",
            "NAVER_CLIENT_ID",
            "NAVER_CLIENT_SECRET",
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
            "GOOGLE_CHAT_WEBHOOK_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        created = await bootstrap_credentials_from_env(db, TEST_USER_ID)
        await db.commit()

        assert len(created) == 1
        assert created[0].definition_key == "openai"
        assert created[0].name.startswith(SEED_NAME_PREFIX)
        # Decryption round-trips to the env value.
        decrypted = credential_service.decrypt_data(created[0].data_encrypted)
        assert decrypted == {"api_key": "sk-test-openai"}

    @pytest.mark.asyncio
    async def test_idempotent(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _ensure_user(db)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
        for var in (
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_CSE_ID",
            "NAVER_CLIENT_ID",
            "NAVER_CLIENT_SECRET",
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
            "GOOGLE_CHAT_WEBHOOK_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        first = await bootstrap_credentials_from_env(db, TEST_USER_ID)
        await db.commit()
        second = await bootstrap_credentials_from_env(db, TEST_USER_ID)
        await db.commit()

        assert len(first) == 1
        assert len(second) == 0
        rows = await db.execute(
            select(Credential).where(Credential.user_id == TEST_USER_ID)
        )
        assert len(rows.scalars().all()) == 1

    @pytest.mark.asyncio
    async def test_partial_env_skipped(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing one of two required env vars → skip that spec entirely."""

        await _ensure_user(db)
        monkeypatch.setenv("GOOGLE_API_KEY", "g-key")
        monkeypatch.delenv("GOOGLE_CSE_ID", raising=False)
        for var in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "NAVER_CLIENT_ID",
            "NAVER_CLIENT_SECRET",
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
            "GOOGLE_CHAT_WEBHOOK_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        created = await bootstrap_credentials_from_env(db, TEST_USER_ID)
        await db.commit()

        assert created == []

    @pytest.mark.asyncio
    async def test_missing_user_skipped(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid as _uuid

        monkeypatch.setenv("OPENAI_API_KEY", "x")
        result = await bootstrap_credentials_from_env(
            db, _uuid.UUID("00000000-0000-0000-0000-0000000000ff")
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_multi_field_naver(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _ensure_user(db)
        monkeypatch.setenv("NAVER_CLIENT_ID", "n-id")
        monkeypatch.setenv("NAVER_CLIENT_SECRET", "n-secret")
        for var in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_CSE_ID",
            "GOOGLE_OAUTH_CLIENT_ID",
            "GOOGLE_OAUTH_CLIENT_SECRET",
            "GOOGLE_OAUTH_REFRESH_TOKEN",
            "GOOGLE_CHAT_WEBHOOK_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        created = await bootstrap_credentials_from_env(db, TEST_USER_ID)
        await db.commit()

        assert len(created) == 1
        assert created[0].definition_key == "naver_search"
        decrypted = credential_service.decrypt_data(created[0].data_encrypted)
        assert json.dumps(decrypted, sort_keys=True) == json.dumps(
            {"client_id": "n-id", "client_secret": "n-secret"}, sort_keys=True
        )
