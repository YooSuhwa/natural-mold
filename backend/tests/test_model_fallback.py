"""Tests for ``create_chat_model_with_fallback`` + executor chain walk."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.agent_runtime import model_factory
from app.agent_runtime.executor import AgentConfig, _build_model_with_fallback
from app.agent_runtime.model_factory import (
    _is_fallback_recoverable,
    create_chat_model_with_fallback,
)
from app.credentials.service import encrypt_data
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.credential_audit_log import CredentialAuditLog
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeChatModel:
    """Stand-in for a LangChain BaseChatModel; behaviour is irrelevant here."""

    def __init__(self, marker: str) -> None:
        self.marker = marker


def _wire_create_model_failures(*, fail_count: int, recoverable: bool = True) -> Any:
    """Patch ``create_chat_model`` so the first ``fail_count`` calls raise."""

    calls: list[tuple[str, str]] = []

    def fake(provider, model_name, *args, **kwargs):
        calls.append((provider, model_name))
        if len(calls) <= fail_count:
            if recoverable:
                # 401 is the canonical "switch credential" trigger.
                response = httpx.Response(
                    401, request=httpx.Request("POST", "https://example/v1/chat")
                )
                raise httpx.HTTPStatusError(
                    "unauthorized", request=response.request, response=response
                )
            raise TypeError("non-recoverable")
        return _FakeChatModel(marker=f"{provider}/{model_name}")

    return calls, fake


async def _seed_credential() -> tuple[uuid.UUID, str]:
    """Insert a User + Credential and return ``(credential_id, plaintext_key)``."""

    plaintext = "sk-fallback-test"
    blob, key_id, field_keys = encrypt_data({"api_key": plaintext})
    cred_id = uuid.uuid4()
    async with TestSession() as db:
        db.add(User(id=TEST_USER_ID, email="t@t", name="T"))
        db.add(
            Credential(
                id=cred_id,
                user_id=TEST_USER_ID,
                definition_key="openai",
                name="primary",
                data_encrypted=blob,
                key_id=key_id,
                field_keys=field_keys,
            )
        )
        await db.commit()
    return cred_id, plaintext


# ---------------------------------------------------------------------------
# Recoverable error classifier
# ---------------------------------------------------------------------------


def test_recoverable_classifier_accepts_documented_codes() -> None:
    for status in (401, 403, 404, 408, 409, 429, 500, 502, 503, 504):
        response = httpx.Response(
            status, request=httpx.Request("POST", "https://example/v1/x")
        )
        exc = httpx.HTTPStatusError("x", request=response.request, response=response)
        assert _is_fallback_recoverable(exc), f"{status} should be recoverable"


def test_recoverable_classifier_rejects_programmer_errors() -> None:
    assert not _is_fallback_recoverable(TypeError("bad arg"))
    assert not _is_fallback_recoverable(ValueError("bad value"))


def test_recoverable_classifier_accepts_timeout_and_connection() -> None:
    assert _is_fallback_recoverable(TimeoutError("slow"))
    assert _is_fallback_recoverable(ConnectionError("network"))


# ---------------------------------------------------------------------------
# Executor-side chain walk (DB-free)
# ---------------------------------------------------------------------------


def test_executor_chain_uses_fallback_when_primary_fails() -> None:
    calls, fake = _wire_create_model_failures(fail_count=1)
    cfg = AgentConfig(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="hi",
        tools_config=[],
        thread_id="t",
        model_fallback_chain=[
            {"provider": "anthropic", "model_name": "claude-sonnet", "base_url": None}
        ],
    )
    with patch("app.agent_runtime.executor.create_chat_model", side_effect=fake):
        result = _build_model_with_fallback(cfg)
    assert isinstance(result, _FakeChatModel)
    assert result.marker == "anthropic/claude-sonnet"
    assert calls == [("openai", "gpt-4o"), ("anthropic", "claude-sonnet")]


def test_executor_chain_re_raises_when_all_fail() -> None:
    calls, fake = _wire_create_model_failures(fail_count=3)
    cfg = AgentConfig(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="hi",
        tools_config=[],
        thread_id="t",
        model_fallback_chain=[
            {"provider": "anthropic", "model_name": "claude-sonnet", "base_url": None},
            {"provider": "google", "model_name": "gemini-pro", "base_url": None},
        ],
    )
    with (
        patch("app.agent_runtime.executor.create_chat_model", side_effect=fake),
        pytest.raises(httpx.HTTPStatusError),
    ):
        _build_model_with_fallback(cfg)
    assert calls == [
        ("openai", "gpt-4o"),
        ("anthropic", "claude-sonnet"),
        ("google", "gemini-pro"),
    ]


def test_executor_chain_skips_fallback_for_unrecoverable_errors() -> None:
    calls, fake = _wire_create_model_failures(fail_count=1, recoverable=False)
    cfg = AgentConfig(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="hi",
        tools_config=[],
        thread_id="t",
        model_fallback_chain=[
            {"provider": "anthropic", "model_name": "claude-sonnet", "base_url": None}
        ],
    )
    with (
        patch("app.agent_runtime.executor.create_chat_model", side_effect=fake),
        pytest.raises(TypeError),
    ):
        _build_model_with_fallback(cfg)
    # Only the primary attempt — the chain should not advance on programmer errors.
    assert calls == [("openai", "gpt-4o")]


def test_executor_chain_no_fallback_keeps_legacy_behaviour() -> None:
    calls, fake = _wire_create_model_failures(fail_count=0)
    cfg = AgentConfig(
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="hi",
        tools_config=[],
        thread_id="t",
        model_fallback_chain=None,
    )
    with patch("app.agent_runtime.executor.create_chat_model", side_effect=fake):
        result = _build_model_with_fallback(cfg)
    assert isinstance(result, _FakeChatModel)
    assert result.marker == "openai/gpt-4o"
    assert calls == [("openai", "gpt-4o")]


# ---------------------------------------------------------------------------
# create_chat_model_with_fallback (ORM + audit log)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_fallback_records_audit_on_each_attempt() -> None:
    cred_id, _ = await _seed_credential()
    primary_id = uuid.uuid4()
    fallback_id = uuid.uuid4()

    async with TestSession() as db:
        primary = Model(
            id=primary_id, provider="openai", model_name="gpt-4o", display_name="GPT-4o"
        )
        fallback = Model(
            id=fallback_id,
            provider="anthropic",
            model_name="claude-sonnet",
            display_name="Claude",
        )
        agent = Agent(
            user_id=TEST_USER_ID,
            name="A",
            system_prompt="hi",
            model_id=primary_id,
            llm_credential_id=cred_id,
            model_fallback_list=[str(fallback_id)],
        )
        agent.model = primary  # type: ignore[assignment]
        db.add_all([primary, fallback, agent])
        await db.commit()
        # Re-load with the relationship populated.
        from sqlalchemy.orm import selectinload

        agent_loaded = (
            await db.execute(
                __import__(
                    "sqlalchemy", fromlist=["select"]
                ).select(Agent)
                .where(Agent.id == agent.id)
                .options(selectinload(Agent.model))
            )
        ).scalar_one()

        _, fake = _wire_create_model_failures(fail_count=1)
        with patch.object(model_factory, "create_chat_model", side_effect=fake):
            chat = await create_chat_model_with_fallback(
                agent_loaded, db, api_key="sk-test"
            )
        assert isinstance(chat, _FakeChatModel)

    async with TestSession() as db:
        from sqlalchemy import select

        rows = (
            await db.execute(
                select(CredentialAuditLog).where(
                    CredentialAuditLog.credential_id == cred_id,
                    CredentialAuditLog.action == "fallback",
                )
            )
        ).scalars().all()
        # One failure (primary) + one success (fallback) = 2 audit rows.
        assert len(rows) == 2
        successes = [r for r in rows if (r.log_metadata or {}).get("success") is True]
        failures = [r for r in rows if (r.log_metadata or {}).get("success") is False]
        assert len(successes) == 1
        assert len(failures) == 1
        assert successes[0].log_metadata["model_name"] == "claude-sonnet"
        assert failures[0].error is not None


@pytest.mark.asyncio
async def test_with_fallback_no_chain_returns_primary() -> None:
    cred_id, _ = await _seed_credential()
    primary_id = uuid.uuid4()
    async with TestSession() as db:
        primary = Model(
            id=primary_id, provider="openai", model_name="gpt-4o", display_name="GPT-4o"
        )
        agent = Agent(
            user_id=TEST_USER_ID,
            name="A",
            system_prompt="hi",
            model_id=primary_id,
            llm_credential_id=cred_id,
            model_fallback_list=None,
        )
        agent.model = primary  # type: ignore[assignment]
        db.add_all([primary, agent])
        await db.commit()

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        agent_loaded = (
            await db.execute(
                select(Agent).where(Agent.id == agent.id).options(selectinload(Agent.model))
            )
        ).scalar_one()

        _, fake = _wire_create_model_failures(fail_count=0)
        with patch.object(model_factory, "create_chat_model", side_effect=fake):
            result = await create_chat_model_with_fallback(
                agent_loaded, db, api_key="sk-test"
            )
        assert isinstance(result, _FakeChatModel)

    # No fallback list → no audit rows
    async with TestSession() as db:
        from sqlalchemy import select

        rows = (
            await db.execute(
                select(CredentialAuditLog).where(
                    CredentialAuditLog.credential_id == cred_id,
                    CredentialAuditLog.action == "fallback",
                )
            )
        ).scalars().all()
        assert len(rows) == 0
