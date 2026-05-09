"""bootstrap_system_credentials — env → ``is_system=True`` Credential seed."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from app.seed.bootstrap_from_env import (
    SEED_NAME_PREFIX,
    bootstrap_system_credentials,
)


class TestBootstrap:
    @pytest.mark.asyncio
    async def test_creates_openai_credential_from_env(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        created = await bootstrap_system_credentials(db)
        await db.commit()

        assert len(created) == 1
        assert created[0].definition_key == "openai"
        assert created[0].name.startswith(SEED_NAME_PREFIX)
        # System credentials are operator-owned, not user-bound.
        assert created[0].is_system is True
        assert created[0].user_id is None
        # Decryption round-trips to the env value.
        decrypted = credential_service.decrypt_data(created[0].data_encrypted)
        assert decrypted == {"api_key": "sk-test-openai"}

    @pytest.mark.asyncio
    async def test_idempotent(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        first = await bootstrap_system_credentials(db)
        await db.commit()
        second = await bootstrap_system_credentials(db)
        await db.commit()

        assert len(first) == 1
        assert len(second) == 0
        rows = await db.execute(
            select(Credential).where(Credential.is_system.is_(True))
        )
        assert len(rows.scalars().all()) == 1

    @pytest.mark.asyncio
    async def test_partial_env_skipped(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing one of two required env vars → skip that spec entirely."""

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

        created = await bootstrap_system_credentials(db)
        await db.commit()

        assert created == []

    @pytest.mark.asyncio
    async def test_multi_field_naver(
        self, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        created = await bootstrap_system_credentials(db)
        await db.commit()

        assert len(created) == 1
        assert created[0].definition_key == "naver_search"
        decrypted = credential_service.decrypt_data(created[0].data_encrypted)
        assert json.dumps(decrypted, sort_keys=True) == json.dumps(
            {"client_id": "n-id", "client_secret": "n-secret"}, sort_keys=True
        )
