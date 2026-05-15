"""Regression guards for ADR-013 — Service LLM Key from Credentials.

Covers ``credential_service.get_provider_keys`` (bulk reader) and
``model_factory.sync_env_fallback_from_credentials`` (in-place dict update).

Priority decisions enforced here:
1. ``.env`` keys (captured at import in ``_ENV_DEFAULTS``) win over credentials.
2. Among credentials, ``is_system=True`` rows beat user rows for the same
   provider.
3. CRUD endpoints (POST/PATCH/DELETE on ``/api/credentials`` and the
   ``/api/system-credentials`` mirror) refresh the dict in-place so the
   ``PROVIDER_API_KEY_MAP`` alias used by builder/assistant helpers stays
   consistent without a server restart.
4. Non-LLM definitions (``naver_search``, ``http_bearer``, …) skip the hook.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import model_factory
from app.credentials import service as credential_service
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    """Ensure the mock test user exists for FK constraints."""

    from sqlalchemy import select

    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


@pytest.fixture(autouse=True)
def _restore_env_fallback():
    """Snapshot/restore ``_ENV_FALLBACK`` so cross-test mutation doesn't leak.

    Tests in this module mutate the dict in-place (intentional — that's the
    contract under test). Restore from ``_ENV_DEFAULTS`` after each run so
    other modules see the import-time .env state.
    """

    snapshot = dict(model_factory._ENV_FALLBACK)
    yield
    model_factory._ENV_FALLBACK.clear()
    model_factory._ENV_FALLBACK.update(snapshot)


def _clear_env_fallback() -> None:
    """Wipe runtime ENV map for tests that assert credential-only behaviour."""

    for key in list(model_factory._ENV_FALLBACK):
        model_factory._ENV_FALLBACK[key] = ""
    # Also re-seed defaults to empty so sync resets to "no .env" state.
    for key in list(model_factory._ENV_DEFAULTS):
        model_factory._ENV_DEFAULTS[key] = ""


# -- Helper: get_provider_keys -----------------------------------------------


@pytest.mark.asyncio
async def test_get_provider_keys_decrypts_anthropic(db: AsyncSession) -> None:
    """``get_provider_keys`` decrypts the api_key from a user credential and
    returns it under the ``_ENV_FALLBACK`` key (``anthropic`` 1:1)."""

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="Test Anthropic",
        data={"api_key": "sk-decrypt-test"},
    )
    await db.commit()

    keys = await credential_service.get_provider_keys(db)

    assert keys.get("anthropic") == "sk-decrypt-test"
    # No openai credential → key absent (caller decides fallback policy).
    assert "openai" not in keys


@pytest.mark.asyncio
async def test_get_provider_keys_maps_google_genai_to_google(
    db: AsyncSession,
) -> None:
    """``google_genai`` definition maps to ``google`` env_key (ADR-013 §결정 4)."""

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="google_genai",
        name="Test Gemini",
        data={"api_key": "gem-key"},
    )
    await db.commit()

    keys = await credential_service.get_provider_keys(db)

    assert keys.get("google") == "gem-key"
    # The credential definition_key itself is NOT a valid _ENV_FALLBACK slot.
    assert "google_genai" not in keys


@pytest.mark.asyncio
async def test_get_provider_keys_system_beats_user(db: AsyncSession) -> None:
    """When both system and user credentials exist for the same provider,
    the system row wins (ADR-013 priority: system > user)."""

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="user anthropic",
        data={"api_key": "user-key"},
    )
    await credential_service.create(
        db,
        user_id=None,
        definition_key="anthropic",
        name="system anthropic",
        data={"api_key": "system-key"},
        is_system=True,
    )
    await db.commit()

    keys = await credential_service.get_provider_keys(db)

    assert keys.get("anthropic") == "system-key"


@pytest.mark.asyncio
async def test_is_llm_definition_membership() -> None:
    """``is_llm_definition`` matches the ADR-013 mapping. ``openai_compatible``
    is intentionally excluded (base_url triple, builder-unsupported)."""

    assert credential_service.is_llm_definition("anthropic")
    assert credential_service.is_llm_definition("openai")
    assert credential_service.is_llm_definition("google_genai")
    assert credential_service.is_llm_definition("openrouter")
    assert not credential_service.is_llm_definition("openai_compatible")
    assert not credential_service.is_llm_definition("naver_search")
    assert not credential_service.is_llm_definition("http_bearer")


# -- sync_env_fallback_from_credentials --------------------------------------


@pytest.mark.asyncio
async def test_sync_writes_credential_when_env_empty(
    db: AsyncSession,
) -> None:
    """When ``.env`` slot is empty, the credential key fills the dict."""

    _clear_env_fallback()

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="cred anthropic",
        data={"api_key": "sk-cred-only"},
    )
    await db.commit()

    await model_factory.sync_env_fallback_from_credentials(db)

    assert model_factory._ENV_FALLBACK["anthropic"] == "sk-cred-only"
    # PROVIDER_API_KEY_MAP alias must reflect the same dict (no rebind).
    assert model_factory.PROVIDER_API_KEY_MAP["anthropic"] == "sk-cred-only"


@pytest.mark.asyncio
async def test_env_key_takes_priority_over_credential(
    db: AsyncSession,
) -> None:
    """Backward compat — ``.env`` value (captured in ``_ENV_DEFAULTS``) wins."""

    # Simulate a deployment with ANTHROPIC_API_KEY=env-override in .env.
    model_factory._ENV_DEFAULTS["anthropic"] = "env-override"
    model_factory._ENV_FALLBACK["anthropic"] = "env-override"

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="cred anthropic",
        data={"api_key": "cred-key"},
    )
    await db.commit()

    await model_factory.sync_env_fallback_from_credentials(db)

    assert model_factory._ENV_FALLBACK["anthropic"] == "env-override"


@pytest.mark.asyncio
async def test_sync_is_idempotent_after_credential_delete(
    db: AsyncSession,
) -> None:
    """Calling sync after a credential is removed wipes the previously-written
    slot back to the .env default — DELETE invalidate path relies on this."""

    _clear_env_fallback()

    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="will-be-deleted",
        data={"api_key": "sk-temp"},
    )
    await db.commit()

    await model_factory.sync_env_fallback_from_credentials(db)
    assert model_factory._ENV_FALLBACK["anthropic"] == "sk-temp"

    await db.delete(cred)
    await db.commit()

    await model_factory.sync_env_fallback_from_credentials(db)
    assert model_factory._ENV_FALLBACK["anthropic"] == ""


# -- Lifespan + CRUD invalidate hooks ----------------------------------------


@pytest.mark.asyncio
async def test_credential_create_invalidates_env_fallback(
    client: AsyncClient,
) -> None:
    """POST /api/credentials with an LLM definition_key must update the dict."""

    _clear_env_fallback()

    response = await client.post(
        "/api/credentials",
        json={
            "definition_key": "anthropic",
            "name": "post-anthropic",
            "data": {"api_key": "sk-post-456"},
        },
    )
    assert response.status_code == 201, response.text

    assert model_factory._ENV_FALLBACK["anthropic"] == "sk-post-456"


@pytest.mark.asyncio
async def test_credential_update_invalidates_env_fallback(
    client: AsyncClient,
) -> None:
    """PATCH /api/credentials/{id} rewrites the dict slot."""

    _clear_env_fallback()

    create_resp = await client.post(
        "/api/credentials",
        json={
            "definition_key": "anthropic",
            "name": "patch-anthropic",
            "data": {"api_key": "sk-old"},
        },
    )
    assert create_resp.status_code == 201
    cred_id = create_resp.json()["id"]
    assert model_factory._ENV_FALLBACK["anthropic"] == "sk-old"

    patch_resp = await client.patch(
        f"/api/credentials/{cred_id}",
        json={"data": {"api_key": "sk-new"}},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    assert model_factory._ENV_FALLBACK["anthropic"] == "sk-new"


@pytest.mark.asyncio
async def test_credential_delete_invalidates_env_fallback(
    client: AsyncClient,
) -> None:
    """DELETE /api/credentials/{id} clears the dict slot back to .env default."""

    _clear_env_fallback()

    create_resp = await client.post(
        "/api/credentials",
        json={
            "definition_key": "anthropic",
            "name": "del-anthropic",
            "data": {"api_key": "sk-tmp"},
        },
    )
    assert create_resp.status_code == 201
    cred_id = create_resp.json()["id"]
    assert model_factory._ENV_FALLBACK["anthropic"] == "sk-tmp"

    del_resp = await client.delete(f"/api/credentials/{cred_id}")
    assert del_resp.status_code == 204

    assert model_factory._ENV_FALLBACK["anthropic"] == ""


@pytest.mark.asyncio
async def test_non_llm_credential_does_not_touch_env_fallback(
    client: AsyncClient,
) -> None:
    """Non-LLM definitions (e.g. ``naver_search``) must skip the hook so we
    don't pay the decrypt cost on every unrelated CRUD call."""

    model_factory._ENV_FALLBACK["anthropic"] = "preserve-me"

    resp = await client.post(
        "/api/credentials",
        json={
            "definition_key": "naver_search",
            "name": "naver",
            "data": {"client_id": "id", "client_secret": "secret"},
        },
    )
    assert resp.status_code == 201

    # Anthropic slot untouched — proves the hook short-circuited.
    assert model_factory._ENV_FALLBACK["anthropic"] == "preserve-me"


@pytest.mark.asyncio
async def test_lifespan_syncs_credentials_to_env_fallback(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Startup hook (called via direct invocation here, since the test client
    overrides ``get_db`` with a different session) writes the user credential
    into the dict when ``.env`` is empty."""

    _clear_env_fallback()

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="openai",
        name="startup-openai",
        data={"api_key": "sk-startup"},
    )
    await db.commit()

    # Simulate the lifespan-time call.
    await model_factory.sync_env_fallback_from_credentials(db)

    assert model_factory._ENV_FALLBACK["openai"] == "sk-startup"


# -- Defensive: openai_compatible skip + bad cipher resilience ---------------


@pytest.mark.asyncio
async def test_openai_compatible_credential_skipped(db: AsyncSession) -> None:
    """``openai_compatible`` doesn't map to any ``_ENV_FALLBACK`` slot — the
    helper must omit it (builder/assistant don't consume base_url triples)."""

    _clear_env_fallback()

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="openai_compatible",
        name="self-hosted",
        data={"api_key": "sk-compat", "base_url": "https://example.com"},
    )
    await db.commit()

    keys = await credential_service.get_provider_keys(db)

    assert "openai_compatible" not in keys
    # All standard slots remain absent.
    assert keys == {}


@pytest.mark.asyncio
async def test_sync_swallows_decrypt_failure(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt credential blob must not crash startup or CRUD handlers."""

    _clear_env_fallback()

    await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="anthropic",
        name="broken",
        data={"api_key": "sk-fine"},
    )
    await db.commit()

    async def _boom(_blob: str) -> dict[str, str]:
        raise RuntimeError("cipher misconfigured")

    monkeypatch.setattr(credential_service, "decrypt_with_external", _boom)

    # Must not raise — sync logs and continues.
    await model_factory.sync_env_fallback_from_credentials(db)

    # Slot stays at the .env default (empty).
    assert model_factory._ENV_FALLBACK.get("anthropic", "") == ""


# Sanity: ensure new symbols are exported.
def test_module_exports_new_symbols() -> None:
    assert hasattr(credential_service, "LLM_DEFINITION_TO_ENV_KEY")
    assert hasattr(credential_service, "get_provider_keys")
    assert hasattr(credential_service, "is_llm_definition")
    assert hasattr(model_factory, "sync_env_fallback_from_credentials")
    # Alias still references the same dict object — critical for the hook
    # contract (mutation is observed without rebinding the name).
    assert model_factory.PROVIDER_API_KEY_MAP is model_factory._ENV_FALLBACK


def test_uuid_module_imported() -> None:
    """Sanity check — ensures uuid usage compiles."""

    assert uuid.UUID("00000000-0000-0000-0000-000000000001") == TEST_USER_ID
