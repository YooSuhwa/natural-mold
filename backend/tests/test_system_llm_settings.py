"""Tests for System LLM settings (ADR-019) — M2 resolver + M4 router."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.system_llm_setting import SystemLlmSetting
from app.services.system_credential_resolver import (
    SystemModelNotConfiguredError,
    resolve_system_model,
)

pytestmark = pytest.mark.asyncio

BASE = "/api/system-llm-settings"


async def _make_system_credential(
    db: AsyncSession,
    *,
    definition_key: str,
    data: dict,
    name: str = "sys-cred",
) -> uuid.UUID:
    cred = await credential_service.create(
        db,
        user_id=None,
        definition_key=definition_key,
        name=name,
        data=data,
        is_system=True,
    )
    await db.commit()
    return cred.id


# --------------------------------------------------------------------------- #
# M4 router — GET
# --------------------------------------------------------------------------- #


async def test_get_returns_three_unconfigured_roles(client: AsyncClient) -> None:
    resp = await client.get(BASE)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    roles = {row["role"] for row in body}
    assert roles == {"text_primary", "text_fallback", "image"}
    for row in body:
        assert row["configured"] is False
        assert row["credential_id"] is None
        assert row["provider"] is None
        assert row["model_name"] is None


# --------------------------------------------------------------------------- #
# M4 router — PUT happy paths
# --------------------------------------------------------------------------- #


async def test_put_selects_llm_credential(client: AsyncClient, db: AsyncSession) -> None:
    cred_id = await _make_system_credential(
        db, definition_key="openai", data={"api_key": "sk-test"}
    )
    resp = await client.put(
        f"{BASE}/text_primary",
        json={"credential_id": str(cred_id), "model_name": "gpt-5.4"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "text_primary"
    assert body["credential_id"] == str(cred_id)
    assert body["provider"] == "openai"
    assert body["model_name"] == "gpt-5.4"
    assert body["configured"] is True
    assert body["base_url"] is None


async def test_put_openai_compatible_exposes_base_url(
    client: AsyncClient, db: AsyncSession
) -> None:
    cred_id = await _make_system_credential(
        db,
        definition_key="openai_compatible",
        data={"api_key": "sk-x", "base_url": "https://litellm.local/v1"},
    )
    resp = await client.put(
        f"{BASE}/image",
        json={"credential_id": str(cred_id), "model_name": "gemini-flash-image"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "openai_compatible"
    assert body["base_url"] == "https://litellm.local/v1"
    assert body["configured"] is True


async def test_put_clear_resets_slot(client: AsyncClient, db: AsyncSession) -> None:
    cred_id = await _make_system_credential(
        db, definition_key="anthropic", data={"api_key": "sk-a"}
    )
    await client.put(
        f"{BASE}/text_fallback",
        json={"credential_id": str(cred_id), "model_name": "claude-sonnet-4-6"},
    )
    resp = await client.put(
        f"{BASE}/text_fallback",
        json={"credential_id": None, "model_name": None},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is False
    assert body["credential_id"] is None


# --------------------------------------------------------------------------- #
# M4 router — validation / error paths (enumeration oracle unified message)
# --------------------------------------------------------------------------- #


async def test_put_unknown_role_404(client: AsyncClient) -> None:
    resp = await client.put(f"{BASE}/bogus", json={"credential_id": None, "model_name": None})
    assert resp.status_code == 404


async def test_put_nonexistent_credential_404(client: AsyncClient) -> None:
    resp = await client.put(
        f"{BASE}/text_primary",
        json={"credential_id": str(uuid.uuid4()), "model_name": "x"},
    )
    assert resp.status_code == 404
    assert "system LLM credential" in resp.json()["error"]["message"]


async def test_put_non_llm_credential_422(client: AsyncClient, db: AsyncSession) -> None:
    cred_id = await _make_system_credential(
        db,
        definition_key="naver_search",
        data={"client_id": "id", "client_secret": "secret"},
    )
    resp = await client.put(
        f"{BASE}/text_primary",
        json={"credential_id": str(cred_id), "model_name": "x"},
    )
    assert resp.status_code == 422
    # Same detail message as the 404 path — reason logged server-side only.
    assert "system LLM credential" in resp.json()["error"]["message"]


async def test_put_user_credential_rejected(client: AsyncClient, db: AsyncSession) -> None:
    # A non-system (user) credential must not be selectable as a system slot.
    cred = await credential_service.create(
        db,
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        definition_key="openai",
        name="user-key",
        data={"api_key": "sk-user"},
        is_system=False,
    )
    await db.commit()
    resp = await client.put(
        f"{BASE}/text_primary",
        json={"credential_id": str(cred.id), "model_name": "gpt-5.4"},
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# M2 resolver
# --------------------------------------------------------------------------- #


async def test_resolve_raises_when_unconfigured(db: AsyncSession) -> None:
    with pytest.raises(SystemModelNotConfiguredError) as exc:
        await resolve_system_model(db, "text_primary")
    assert exc.value.role == "text_primary"


async def test_resolve_returns_model_with_base_url(db: AsyncSession) -> None:
    cred_id = await _make_system_credential(
        db,
        definition_key="openrouter",
        data={"api_key": "sk-or", "base_url": "https://openrouter.ai/api/v1"},
    )
    db.add(
        SystemLlmSetting(
            role="text_primary",
            credential_id=cred_id,
            model_name="anthropic/claude-sonnet-4.6",
        )
    )
    await db.commit()

    resolved = await resolve_system_model(db, "text_primary")
    assert resolved.provider == "openrouter"
    assert resolved.model_name == "anthropic/claude-sonnet-4.6"
    assert resolved.api_key == "sk-or"
    assert resolved.base_url == "https://openrouter.ai/api/v1"


async def test_resolve_raises_when_model_name_missing(db: AsyncSession) -> None:
    cred_id = await _make_system_credential(
        db, definition_key="anthropic", data={"api_key": "sk-a"}
    )
    db.add(SystemLlmSetting(role="image", credential_id=cred_id, model_name=None))
    await db.commit()
    with pytest.raises(SystemModelNotConfiguredError):
        await resolve_system_model(db, "image")


# --------------------------------------------------------------------------- #
# S3 wiring — image base_url resolution (ADR-019 image role)
# --------------------------------------------------------------------------- #


async def test_image_base_url_prefers_payload() -> None:
    from app.services.image_service import (
        ResolvedSystemModel,
        resolve_image_base_url,
    )

    resolved = ResolvedSystemModel(
        provider="openai_compatible",
        model_name="m",
        api_key="k",
        base_url="https://litellm.local/v1/",
    )
    # trailing slash trimmed
    assert resolve_image_base_url(resolved) == "https://litellm.local/v1"


async def test_image_base_url_canonical_fallback() -> None:
    from app.services.image_service import (
        ResolvedSystemModel,
        resolve_image_base_url,
    )

    for provider, expected in (
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("openai", "https://api.openai.com/v1"),
    ):
        resolved = ResolvedSystemModel(
            provider=provider, model_name="m", api_key="k", base_url=None
        )
        assert resolve_image_base_url(resolved) == expected


async def test_image_base_url_raises_for_unknown_provider_without_base_url() -> None:
    from app.services.image_service import (
        ResolvedSystemModel,
        resolve_image_base_url,
    )

    resolved = ResolvedSystemModel(provider="anthropic", model_name="m", api_key="k", base_url=None)
    with pytest.raises(ValueError, match="base_url"):
        resolve_image_base_url(resolved)


# --------------------------------------------------------------------------- #
# S3 wiring — assistant surfaces SystemModelNotConfiguredError as SSE error
# --------------------------------------------------------------------------- #


async def test_assistant_stream_surfaces_unconfigured(db: AsyncSession) -> None:
    from app.services.assistant_service import stream_assistant_message

    chunks = [
        chunk
        async for chunk in stream_assistant_message(
            db=db,
            agent_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            thread_id="assistant_test",
            user_message="hi",
        )
    ]
    # text_primary unconfigured (no seed in test DB) → single SSE error event.
    assert len(chunks) == 1
    assert "event: error" in chunks[0]
    assert "system_model_not_configured" in chunks[0]


# --------------------------------------------------------------------------- #
# S2 hardening (bezos review) — super_user guard + enumeration oracle parity
# --------------------------------------------------------------------------- #


@pytest.fixture
async def non_super_client():
    """Client whose current user is authenticated but NOT a super_user."""
    from httpx import ASGITransport

    from app.dependencies import (
        CurrentUser,
        get_current_user,
        get_db,
        verify_csrf,
    )
    from app.main import create_app
    from tests.conftest import override_get_db

    async def _override_non_super() -> CurrentUser:
        return CurrentUser(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            email="user@test.com",
            name="Regular User",
            is_super_user=False,
        )

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = _override_non_super
    app.dependency_overrides[verify_csrf] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_get_requires_super_user(non_super_client: AsyncClient) -> None:
    resp = await non_super_client.get(BASE)
    assert resp.status_code == 403


async def test_put_requires_super_user(non_super_client: AsyncClient) -> None:
    resp = await non_super_client.put(
        f"{BASE}/text_primary",
        json={"credential_id": None, "model_name": None},
    )
    assert resp.status_code == 403


async def test_invalid_credential_detail_is_byte_identical(
    client: AsyncClient, db: AsyncSession
) -> None:
    """404 (missing) and 422 (non-LLM) must return the *same* detail string so
    the response can't be used to enumerate which system credentials exist."""
    missing = await client.put(
        f"{BASE}/text_primary",
        json={"credential_id": str(uuid.uuid4()), "model_name": "x"},
    )
    cred_id = await _make_system_credential(
        db,
        definition_key="naver_search",
        data={"client_id": "id", "client_secret": "secret"},
    )
    wrong_type = await client.put(
        f"{BASE}/text_fallback",
        json={"credential_id": str(cred_id), "model_name": "x"},
    )
    assert missing.status_code == 404
    assert wrong_type.status_code == 422
    assert missing.json()["error"]["message"] == wrong_type.json()["error"]["message"]


async def test_role_unique_constraint(db: AsyncSession) -> None:
    """Duplicate role rows are rejected at the DB layer (UNIQUE(role))."""
    import sqlalchemy.exc

    db.add(SystemLlmSetting(role="text_primary"))
    db.add(SystemLlmSetting(role="text_primary"))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await db.commit()


async def test_credential_delete_sets_slot_null() -> None:
    """Deleting the bound credential transitions the slot to NULL (FK SET NULL).

    Uses a *dedicated* engine with ``PRAGMA foreign_keys=ON`` — the shared
    conftest engine leaves FK enforcement off, which would let SQLite silently
    skip the SET NULL action (false pass). Scoped here so the global suite is
    untouched (ADR019-OPEN-2 tracks adopting it suite-wide). PostgreSQL enforces
    this natively (verified via the m45 alembic round-trip).
    """
    from sqlalchemy import event, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models.credential import Credential

    eng = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(eng, expire_on_commit=False)
        async with session_factory() as db:
            cred = await credential_service.create(
                db,
                user_id=None,
                definition_key="openai",
                name="sys-openai",
                data={"api_key": "sk-x"},
                is_system=True,
            )
            await db.commit()
            cred_id = cred.id

            db.add(
                SystemLlmSetting(
                    role="text_primary",
                    credential_id=cred_id,
                    model_name="gpt-5.4",
                )
            )
            await db.commit()

            cred_row = (
                await db.execute(select(Credential).where(Credential.id == cred_id))
            ).scalar_one()
            await db.delete(cred_row)
            await db.commit()

            setting = (
                await db.execute(
                    select(SystemLlmSetting).where(SystemLlmSetting.role == "text_primary")
                )
            ).scalar_one()
            # Slot still exists, but credential reference is cleared.
            assert setting.credential_id is None
    finally:
        await eng.dispose()
