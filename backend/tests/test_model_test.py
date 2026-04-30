"""Tests for ``app.services.model_test`` + ``/api/models/{id}/test`` router.

Provider SDKs are stubbed at the LangChain layer (``ChatOpenAI.ainvoke`` etc.)
so the suite never reaches the network. The probe under test is the Moldy
glue: classification, curl rendering, audit-log emission, masking.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential_audit_log import CredentialAuditLog
from app.models.model import Model
from app.models.user import User
from app.services import model_test
from app.services.model_test import (
    ModelTestResult,
    _build_curl,
    _classify,
    _clean_error_message,
    _reconstruct_request,
    run_model_test,
)
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# Test fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="mt@test", name="mt"))
        await db.commit()


def _stub_ainvoke(content: str, *, tokens_in: int = 5, tokens_out: int = 1):
    """Patch the LangChain chat model's ``ainvoke`` with a deterministic stub."""

    async def _ainvoke(self, messages):  # noqa: ARG001 — protocol signature
        msg = AIMessage(content=content)
        msg.usage_metadata = {  # type: ignore[attr-defined]
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        }
        return msg

    from langchain_openai import ChatOpenAI

    return patch.object(ChatOpenAI, "ainvoke", _ainvoke)


def _raise_ainvoke(exc: Exception):
    """Patch ``ChatOpenAI.ainvoke`` to raise ``exc`` synchronously."""

    async def _ainvoke(self, messages):  # noqa: ARG001
        raise exc

    from langchain_openai import ChatOpenAI

    return patch.object(ChatOpenAI, "ainvoke", _ainvoke)


def _delay_ainvoke(seconds: float):
    async def _ainvoke(self, messages):  # noqa: ARG001
        await asyncio.sleep(seconds)
        return AIMessage(content="too late")

    from langchain_openai import ChatOpenAI

    return patch.object(ChatOpenAI, "ainvoke", _ainvoke)


# ---------------------------------------------------------------------------
# Pure helpers — clean error / curl / classify
# ---------------------------------------------------------------------------


def test_clean_error_strips_provider_prefix() -> None:
    raw = "litellm.AuthenticationError: invalid api key"
    assert _clean_error_message(raw) == "invalid api key"


def test_clean_error_strips_error_code_prefix() -> None:
    raw = "Error code: 401 - the supplied API key is invalid"
    assert _clean_error_message(raw) == "the supplied API key is invalid"


def test_clean_error_strips_stack_trace_and_caps_length() -> None:
    raw = "openai.APIError: bad gateway\nstack trace:\n  ...frames..." + "X" * 500
    cleaned = _clean_error_message(raw)
    assert cleaned.startswith("bad gateway")
    assert "stack trace" not in cleaned
    assert len(cleaned) <= 300


def test_clean_error_blank_returns_unknown() -> None:
    assert _clean_error_message("") == "unknown error"


def test_build_curl_masks_authorization_bearer() -> None:
    request = _reconstruct_request(
        provider="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key="sk-very-secret-do-not-leak",
    )
    curl = _build_curl(request)

    # The placeholder is in the curl, the secret is not.
    assert "${API_KEY}" in curl
    assert "sk-very-secret-do-not-leak" not in curl
    # The reconstructed request body should mention masked Authorization too —
    # the API surface returns it so the UI can show "Authorization: ***".
    assert request["headers"]["Authorization"] == "Bearer ***"


def test_build_curl_masks_anthropic_x_api_key() -> None:
    request = _reconstruct_request(
        provider="anthropic",
        model_name="claude-3-5-haiku-latest",
        base_url=None,
        api_key="sk-ant-secret",
    )
    curl = _build_curl(request)
    assert "sk-ant-secret" not in curl
    assert "${API_KEY}" in curl
    assert "x-api-key" in curl
    assert request["headers"]["x-api-key"] == "***"


def test_build_curl_masks_google_query_string_key() -> None:
    request = _reconstruct_request(
        provider="google",
        model_name="gemini-2.0-flash",
        base_url=None,
        api_key="AIza-secret",
    )
    curl = _build_curl(request)
    assert "AIza-secret" not in curl
    # Replaced ``key=***`` → ``key=${API_KEY}``.
    assert "key=${API_KEY}" in curl


def test_classify_buckets_by_status_code() -> None:
    class _Exc(Exception):
        status_code = 401

    err = _classify(_Exc("unauthorized"))
    assert err.kind == "auth"

    class _NotFound(Exception):
        status_code = 404

    assert _classify(_NotFound("nope")).kind == "not_found"

    class _Rate(Exception):
        status_code = 429

    assert _classify(_Rate("slow down")).kind == "rate_limit"


def test_classify_falls_back_to_message() -> None:
    err = _classify(Exception("Model not found: gpt-99"))
    assert err.kind == "not_found"
    err = _classify(Exception("Rate limit exceeded for org"))
    assert err.kind == "rate_limit"
    err = _classify(Exception("invalid api key"))
    assert err.kind == "auth"


def test_classify_other_for_unknown() -> None:
    err = _classify(Exception("the planets are not aligned"))
    assert err.kind == "other"


def test_classify_timeout_for_httpx_timeout() -> None:
    err = _classify(httpx.TimeoutException("read timeout"))
    assert err.kind == "timeout"


# ---------------------------------------------------------------------------
# run_model_test — happy + error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_model_test_success_with_pricing() -> None:
    with _stub_ainvoke("pong", tokens_in=5, tokens_out=2):
        result = await run_model_test(
            provider="openai",
            model_name="gpt-4o-mini",
            base_url=None,
            credential_data={"api_key": "sk-x"},
            cost_per_input_token=Decimal("0.000001"),
            cost_per_output_token=Decimal("0.000002"),
        )

    assert isinstance(result, ModelTestResult)
    assert result.success is True
    assert result.response == "pong"
    assert result.tokens_in == 5
    assert result.tokens_out == 2
    # 5 * 0.000001 + 2 * 0.000002 = 0.000009
    assert result.estimated_cost_usd == pytest.approx(0.000009, rel=1e-6)
    assert result.error is None
    assert result.curl_command is not None
    assert "${API_KEY}" in result.curl_command
    assert "sk-x" not in result.curl_command


@pytest.mark.asyncio
async def test_run_model_test_success_without_pricing_returns_none_cost() -> None:
    with _stub_ainvoke("pong"):
        result = await run_model_test(
            provider="openai",
            model_name="gpt-4o-mini",
            base_url=None,
            credential_data={"api_key": "sk-x"},
        )
    assert result.success is True
    assert result.estimated_cost_usd is None


@pytest.mark.asyncio
async def test_run_model_test_auth_error() -> None:
    class _Auth(Exception):
        status_code = 401

    with _raise_ainvoke(_Auth("Unauthorized — invalid API key")):
        result = await run_model_test(
            provider="openai",
            model_name="gpt-4o-mini",
            base_url=None,
            credential_data={"api_key": "sk-bad"},
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.kind == "auth"
    # Raw key never appears in any reconstructed surface.
    assert result.curl_command and "sk-bad" not in result.curl_command


@pytest.mark.asyncio
async def test_run_model_test_not_found_error() -> None:
    class _NF(Exception):
        status_code = 404

    with _raise_ainvoke(_NF("the model `gpt-99` does not exist")):
        result = await run_model_test(
            provider="openai",
            model_name="gpt-99",
            base_url=None,
            credential_data={"api_key": "sk-x"},
        )
    assert result.success is False
    assert result.error and result.error.kind == "not_found"


@pytest.mark.asyncio
async def test_run_model_test_rate_limit_error() -> None:
    class _RL(Exception):
        status_code = 429

    with _raise_ainvoke(_RL("rate limit exceeded for org")):
        result = await run_model_test(
            provider="openai",
            model_name="gpt-4o-mini",
            base_url=None,
            credential_data={"api_key": "sk-x"},
        )
    assert result.success is False
    assert result.error and result.error.kind == "rate_limit"


@pytest.mark.asyncio
async def test_run_model_test_timeout(monkeypatch) -> None:
    """Force the timeout by shrinking ``_TEST_TIMEOUT_SECONDS`` and hanging."""

    monkeypatch.setattr(model_test, "_TEST_TIMEOUT_SECONDS", 0.1)

    with _delay_ainvoke(0.5):
        result = await run_model_test(
            provider="openai",
            model_name="gpt-4o-mini",
            base_url=None,
            credential_data={"api_key": "sk-x"},
        )

    assert result.success is False
    assert result.error and result.error.kind == "timeout"


# ---------------------------------------------------------------------------
# Router: /api/models/{id}/test
# ---------------------------------------------------------------------------


async def _create_credential(
    db: AsyncSession, *, definition_key: str = "openai", data: dict | None = None
) -> uuid.UUID:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key=definition_key,
        name=f"{definition_key}-test",
        data=data or {"api_key": "sk-stored"},
    )
    await db.commit()
    return cred.id


async def _create_model(
    db: AsyncSession,
    *,
    provider: str = "openai",
    model_name: str = "gpt-4o-mini",
    cost_in: Decimal | None = Decimal("0.000001"),
    cost_out: Decimal | None = Decimal("0.000002"),
) -> uuid.UUID:
    m = Model(
        provider=provider,
        model_name=model_name,
        display_name=model_name,
        cost_per_input_token=cost_in,
        cost_per_output_token=cost_out,
    )
    db.add(m)
    await db.commit()
    return m.id


@pytest.mark.asyncio
async def test_router_test_registered_model_success(
    client: AsyncClient, db: AsyncSession
) -> None:
    cred_id = await _create_credential(db)
    model_id = await _create_model(db)

    with _stub_ainvoke("pong", tokens_in=4, tokens_out=1):
        response = await client.post(
            f"/api/models/{model_id}/test", params={"credential_id": str(cred_id)}
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["response"] == "pong"
    assert body["tokens_in"] == 4
    assert body["tokens_out"] == 1
    assert body["curl_command"] and "${API_KEY}" in body["curl_command"]


@pytest.mark.asyncio
async def test_router_test_registered_model_writes_audit_log(
    client: AsyncClient, db: AsyncSession
) -> None:
    cred_id = await _create_credential(db)
    model_id = await _create_model(db)

    with _stub_ainvoke("pong"):
        await client.post(
            f"/api/models/{model_id}/test", params={"credential_id": str(cred_id)}
        )

    rows = (
        await db.execute(
            select(CredentialAuditLog).where(CredentialAuditLog.credential_id == cred_id)
        )
    ).scalars().all()
    test_events = [r for r in rows if r.action == "test"]
    assert test_events, "expected a 'test' audit log entry"
    assert test_events[-1].log_metadata is not None
    assert test_events[-1].log_metadata.get("success") is True
    assert test_events[-1].log_metadata.get("model_id") == str(model_id)


@pytest.mark.asyncio
async def test_router_test_registered_model_credential_required(
    client: AsyncClient, db: AsyncSession
) -> None:
    model_id = await _create_model(db)
    response = await client.post(f"/api/models/{model_id}/test")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_router_test_registered_model_404_for_unknown_model(
    client: AsyncClient, db: AsyncSession
) -> None:
    cred_id = await _create_credential(db)
    response = await client.post(
        f"/api/models/{uuid.uuid4()}/test", params={"credential_id": str(cred_id)}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_router_test_registered_model_404_for_unknown_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    model_id = await _create_model(db)
    response = await client.post(
        f"/api/models/{model_id}/test", params={"credential_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Router: /api/models/test-preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_test_preview_success(
    client: AsyncClient, db: AsyncSession
) -> None:
    cred_id = await _create_credential(db)

    with _stub_ainvoke("pong", tokens_in=3, tokens_out=1):
        response = await client.post(
            "/api/models/test-preview",
            json={
                "provider": "openai",
                "model_name": "gpt-4o-mini",
                "credential_id": str(cred_id),
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    # No model row → no pricing → estimated_cost is null.
    assert body["estimated_cost_usd"] is None


@pytest.mark.asyncio
async def test_router_test_preview_failure_classified(
    client: AsyncClient, db: AsyncSession
) -> None:
    cred_id = await _create_credential(db)

    class _NF(Exception):
        status_code = 404

    with _raise_ainvoke(_NF("model not found")):
        response = await client.post(
            "/api/models/test-preview",
            json={
                "provider": "openai",
                "model_name": "ghost-model",
                "credential_id": str(cred_id),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"]["kind"] == "not_found"


@pytest.mark.asyncio
async def test_router_test_preview_404_for_unknown_credential(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/models/test-preview",
        json={
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "credential_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Curl regex sanity (cross-check between helper and router payload)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_curl_never_contains_real_key(
    client: AsyncClient, db: AsyncSession
) -> None:
    cred_id = await _create_credential(db, data={"api_key": "sk-LEAK-DETECTOR"})
    model_id = await _create_model(db)

    with _stub_ainvoke("pong"):
        response = await client.post(
            f"/api/models/{model_id}/test",
            params={"credential_id": str(cred_id)},
        )

    body = response.json()
    blob = response.text + (body.get("curl_command") or "")
    assert "sk-LEAK-DETECTOR" not in blob
    assert "${API_KEY}" in (body.get("curl_command") or "")


def test_curl_command_format_is_shell_safe() -> None:
    """Sanity: rendered curl is single-line-per-flag and contains -X POST."""

    request = _reconstruct_request(
        provider="openai",
        model_name="gpt-4o-mini",
        base_url=None,
        api_key="sk-x",
    )
    curl = _build_curl(request)
    assert curl.startswith("curl -X POST '")
    # ``-d`` block carries the JSON body — match a JSON-ish opener.
    assert re.search(r"-d '\{", curl) is not None
