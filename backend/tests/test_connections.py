"""Tests for Connection CRUD + validators + is_default toggle (백로그 E M1).

ADR-008 §M1 테스트 시나리오 8개.
- 기존 테스트/서비스/모델/라우터는 건드리지 않는다 (parallel run 원칙).
- 시나리오 2/3 (validator)는 Pydantic 스키마 레벨에서 검증한다. HTTP 422 경로에
  별도 `app/main.py:validation_error_handler` 버그(ValueError가 JSON 직렬화 실패)
  가 있어 500이 반환된다. "?" 프로토콜로 사티아/젠슨에게 에스컬레이션 중.
- 시나리오 7 (ON DELETE SET NULL)은 SQLite FK 기본 OFF 때문에 전용 엔진을
  연결 이벤트 리스너와 함께 만들어 수행한다. 공유 conftest 엔진은 건드리지 않아
  다른 테스트에 영향 0.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.connection import Connection
from app.models.credential import Credential
from app.models.user import User
from app.schemas.connection import ConnectionCreate
from tests.conftest import TEST_USER_ID

OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _seed_credential(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    name: str = "Seeded",
) -> Credential:
    cred = Credential(
        user_id=user_id,
        name=name,
        credential_type="api_key",
        provider_name="naver",
        data_encrypted="ciphertext-placeholder",
        field_keys=["naver_client_id", "naver_client_secret"],
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred


# ---------------------------------------------------------------------------
# Scenario 1 — CRUD basics (with credential + with NULL credential)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_basic_with_and_without_credential(
    client: AsyncClient, db: AsyncSession
):
    """POST/GET/PATCH/DELETE cycle works for both credential-bound and NULL connections."""
    cred = await _seed_credential(db)

    # POST with credential_id
    resp = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "회사 네이버 키",
            "credential_id": str(cred.id),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["credential_id"] == str(cred.id)
    assert body["display_name"] == "회사 네이버 키"
    assert body["is_default"] is True  # first in scope — auto default
    bound_id = body["id"]

    # POST without credential (different scope to avoid default interference)
    resp = await client.post(
        "/api/connections",
        json={
            "type": "custom",
            "provider_name": "my_http_api",
            "display_name": "내 HTTP 도구",
            "credential_id": None,
        },
    )
    assert resp.status_code == 201, resp.text
    null_id = resp.json()["id"]
    assert resp.json()["credential_id"] is None

    # GET list
    list_resp = await client.get("/api/connections")
    assert list_resp.status_code == 200
    ids = {row["id"] for row in list_resp.json()}
    assert {bound_id, null_id}.issubset(ids)

    # GET single
    one_resp = await client.get(f"/api/connections/{bound_id}")
    assert one_resp.status_code == 200
    assert one_resp.json()["id"] == bound_id

    # PATCH display_name
    patch_resp = await client.patch(
        f"/api/connections/{bound_id}",
        json={"display_name": "renamed"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "renamed"

    # DELETE both
    for cid in (bound_id, null_id):
        del_resp = await client.delete(f"/api/connections/{cid}")
        assert del_resp.status_code == 204
        assert (await client.get(f"/api/connections/{cid}")).status_code == 404


# ---------------------------------------------------------------------------
# Scenario 2 — MCP validator: extra_config.url required
# (Schema-level; HTTP 422 path blocked by main.py handler bug.)
# ---------------------------------------------------------------------------


def test_mcp_validator_requires_extra_config_url():
    """type='mcp' must require extra_config with url + auth_type (Pydantic level)."""
    # Missing extra_config entirely
    with pytest.raises(ValidationError) as exc:
        ConnectionCreate(
            type="mcp", provider_name="resend", display_name="Resend MCP"
        )
    assert "extra_config" in str(exc.value)

    # extra_config present but url missing
    with pytest.raises(ValidationError) as exc:
        ConnectionCreate(
            type="mcp",
            provider_name="resend",
            display_name="Resend MCP",
            extra_config={"auth_type": "bearer"},
        )
    assert "url" in str(exc.value)

    # auth_type missing
    with pytest.raises(ValidationError) as exc:
        ConnectionCreate(
            type="mcp",
            provider_name="resend",
            display_name="Resend MCP",
            extra_config={"url": "https://x"},
        )
    assert "auth_type" in str(exc.value)

    # Complete payload succeeds
    ok = ConnectionCreate(
        type="mcp",
        provider_name="resend",
        display_name="Resend MCP",
        extra_config={"url": "https://mcp.example.com", "auth_type": "bearer"},
    )
    assert ok.extra_config.url == "https://mcp.example.com"


# ---------------------------------------------------------------------------
# Scenario 3 — PREBUILT validator: provider_name must be in credential_registry
# (Schema-level; HTTP 422 path blocked by main.py handler bug.)
# ---------------------------------------------------------------------------


def test_prebuilt_validator_rejects_non_enum_provider():
    """type='prebuilt' non-enum provider_name → ValidationError; MCP/CUSTOM free-form."""
    with pytest.raises(ValidationError) as exc:
        ConnectionCreate(
            type="prebuilt", provider_name="foo", display_name="Bogus"
        )
    msg = str(exc.value)
    assert "provider_name" in msg and "prebuilt" in msg

    # Known enum values all pass
    for name in ("naver", "google_search", "google_workspace", "google_chat", "custom_api_key"):
        ConnectionCreate(type="prebuilt", provider_name=name, display_name="n")

    # CUSTOM accepts free-form identifiers
    ConnectionCreate(
        type="custom", provider_name="my_internal_api", display_name="x"
    )

    # CUSTOM rejects invalid identifier characters
    with pytest.raises(ValidationError):
        ConnectionCreate(
            type="custom", provider_name="Bad Name!", display_name="x"
        )


# ---------------------------------------------------------------------------
# Scenario 4 — is_default auto set on first connection in scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_default_auto_on_first_connection(client: AsyncClient):
    """First connection in (user, type, provider) scope is_default=True even when unspecified."""
    resp = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "google_search",
            "display_name": "첫 키",
            # is_default omitted — server must still flip to True.
        },
    )
    assert resp.status_code == 201
    assert resp.json()["is_default"] is True


# ---------------------------------------------------------------------------
# Scenario 5 — is_default toggle atomicity: promoting a second connection clears the first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_default_toggle_clears_previous_default(
    client: AsyncClient, db: AsyncSession
):
    """Creating/promoting another default within the same scope demotes the existing default."""
    first = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "첫 키",
        },
    )
    assert first.status_code == 201
    first_id = first.json()["id"]
    assert first.json()["is_default"] is True

    # Second in the same scope, request is_default=True at create time
    second = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "두번째 키",
            "is_default": True,
        },
    )
    assert second.status_code == 201
    assert second.json()["is_default"] is True
    second_id = second.json()["id"]

    # First must have been demoted
    reloaded_first = (
        await db.execute(
            select(Connection).where(Connection.id == uuid.UUID(first_id))
        )
    ).scalar_one()
    assert reloaded_first.is_default is False

    # Now promote first back via PATCH — second should be demoted
    patch_resp = await client.patch(
        f"/api/connections/{first_id}",
        json={"is_default": True},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_default"] is True

    reloaded_second = (
        await db.execute(
            select(Connection).where(Connection.id == uuid.UUID(second_id))
        )
    ).scalar_one()
    assert reloaded_second.is_default is False


# ---------------------------------------------------------------------------
# Scenario 6 — IDOR: user A must not reach user B's connection via GET/PATCH/DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idor_returns_404_for_other_user_rows(
    client: AsyncClient, db: AsyncSession
):
    """Rows owned by another user_id must appear as 404 to the current user."""
    foreign = Connection(
        user_id=OTHER_USER_ID,
        type="prebuilt",
        provider_name="naver",
        display_name="user_B 소유",
        credential_id=None,
        extra_config=None,
        is_default=True,
        status="active",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(foreign)
    await db.commit()
    await db.refresh(foreign)

    fid = str(foreign.id)

    get_resp = await client.get(f"/api/connections/{fid}")
    assert get_resp.status_code == 404

    patch_resp = await client.patch(
        f"/api/connections/{fid}",
        json={"display_name": "pwned"},
    )
    assert patch_resp.status_code == 404

    del_resp = await client.delete(f"/api/connections/{fid}")
    assert del_resp.status_code == 404

    # Row must still exist untouched
    still_there = (
        await db.execute(select(Connection).where(Connection.id == foreign.id))
    ).scalar_one()
    assert still_there.display_name == "user_B 소유"
    assert still_there.user_id == OTHER_USER_ID

    # list must not leak it
    list_resp = await client.get("/api/connections")
    assert list_resp.status_code == 200
    leaked = [row for row in list_resp.json() if row["id"] == fid]
    assert leaked == []


# ---------------------------------------------------------------------------
# Scenario 7 — credential ON DELETE SET NULL: connection survives, FK nulled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credential_delete_sets_connection_credential_id_null():
    """Deleting a credential must null connection.credential_id but keep the row.

    Uses a dedicated engine with FK enforcement enabled via connect event so
    SQLite actually honors ondelete='SET NULL'. The shared conftest engine is
    never touched — no regression risk on other tests.
    """
    fk_engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(fk_engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Session = async_sessionmaker(fk_engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with fk_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with Session() as session:
            # Belt-and-braces: also assert pragma on this logical connection.
            pragma_val = (
                await session.execute(text("PRAGMA foreign_keys"))
            ).scalar()
            assert pragma_val == 1, f"FK pragma not enforced (got {pragma_val})"
            user = User(id=TEST_USER_ID, email="fk@test.com", name="FK User")
            session.add(user)
            await session.flush()

            cred = Credential(
                user_id=TEST_USER_ID,
                name="to-be-deleted",
                credential_type="api_key",
                provider_name="naver",
                data_encrypted="ciphertext",
                field_keys=["naver_client_id", "naver_client_secret"],
            )
            session.add(cred)
            await session.flush()
            cred_id = cred.id

            c = Connection(
                user_id=TEST_USER_ID,
                type="prebuilt",
                provider_name="naver",
                display_name="연결",
                credential_id=cred_id,
                extra_config=None,
                is_default=True,
                status="active",
                created_at=_now(),
                updated_at=_now(),
            )
            session.add(c)
            await session.commit()
            conn_id = c.id
            assert c.credential_id == cred_id

            # Delete credential and verify the FK action. Expire ORM identity
            # map so the reload reflects the database-level SET NULL instead
            # of the cached Python object.
            await session.delete(cred)
            await session.commit()
            session.expire_all()

            fresh = (
                await session.execute(
                    select(Connection).where(Connection.id == conn_id)
                )
            ).scalar_one()
            assert fresh.id == conn_id
            assert fresh.credential_id is None
    finally:
        await fk_engine.dispose()


# ---------------------------------------------------------------------------
# Scenario 8 — extra_config type mismatch: PREBUILT with extra_config
#   Current validator enforces MCP-only required keys. PREBUILT passing
#   extra_config is accepted (stored as-is) — documents the chosen policy.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prebuilt_and_custom_extra_config_rejected(
    client: AsyncClient,
):
    """PREBUILT/CUSTOM은 extra_config를 허용하지 않는다 (Codex adversarial Finding 3).
    평문 시크릿 채널을 만드는 걸 막기 위해 MCP에서만 extra_config 사용."""
    # PREBUILT + extra_config → 422
    resp = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "네이버",
            "extra_config": {"url": "https://irrelevant", "auth_type": "bearer"},
        },
    )
    assert resp.status_code == 422, resp.text

    # CUSTOM + extra_config → 422
    resp = await client.post(
        "/api/connections",
        json={
            "type": "custom",
            "provider_name": "my_api",
            "display_name": "C",
            "extra_config": {"url": "https://x", "auth_type": "none"},
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Extra — PATCH `None` 은 명시적 해제 (exclude_unset 패턴)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_with_none_unlinks_credential(
    client: AsyncClient, db: AsyncSession
):
    """credential_id=None 을 명시적으로 보내면 FK 링크가 해제된다 (미전송과 구분)."""
    cred = await _seed_credential(db)

    created = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "회사",
            "credential_id": str(cred.id),
        },
    )
    assert created.status_code == 201
    conn_id = created.json()["id"]

    # display_name만 갱신 — credential_id 미전송 → 링크 유지돼야 함
    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={"display_name": "회사-renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["credential_id"] == str(cred.id)
    assert resp.json()["display_name"] == "회사-renamed"

    # credential_id=None 명시 전송 → 링크 해제
    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={"credential_id": None},
    )
    assert resp.status_code == 200
    assert resp.json()["credential_id"] is None
    assert resp.json()["display_name"] == "회사-renamed"  # 다른 필드는 보존


# ---------------------------------------------------------------------------
# M10 seed marker — display_name 보호 (downgrade 가역성)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_rejects_reserved_marker_display_name(client: AsyncClient):
    """사용자는 API로 `[m10-auto-seed]` 프리픽스 display_name을 만들 수 없다.

    downgrade의 LIKE 패턴이 사용자 수동 생성분을 잘못 삭제하는 것을 원천 차단
    (Codex adversarial P1 — downgrade data loss 방지).
    """
    resp = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "[m10-auto-seed] naver",
        },
    )
    assert resp.status_code == 422
    body_text = resp.text
    assert "reserved marker" in body_text or "[m10-auto-seed]" in body_text


@pytest.mark.asyncio
async def test_update_rejects_removing_m10_seed_marker(
    client: AsyncClient, db: AsyncSession
):
    """m10 자동 시드 connection의 display_name은 마커 프리픽스를 제거하지 못한다.

    m10이 이미 시드해둔 marker row(DB 직접 insert로 시뮬레이션)에 대해 PATCH로
    마커를 제거하면 400. 마커 유지 rename은 허용. marker 없는 row는 자유 편집.
    """
    cred = await _seed_credential(db)

    # m10 마커가 있는 row를 DB에 직접 insert (API는 marker를 거부하므로 우회)
    seeded = Connection(
        user_id=TEST_USER_ID,
        type="prebuilt",
        provider_name="naver",
        display_name="[m10-auto-seed] naver",
        credential_id=cred.id,
        is_default=True,
        status="active",
    )
    db.add(seeded)
    await db.commit()
    await db.refresh(seeded)
    seeded_id = seeded.id

    # 마커 없는 이름으로 PATCH → 400
    resp = await client.patch(
        f"/api/connections/{seeded_id}",
        json={"display_name": "My Naver Key"},
    )
    assert resp.status_code == 400
    assert "자동 시드" in resp.json().get("detail", "")

    # 마커를 유지한 변경도 422 — PATCH schema validator가 marker 프리픽스를
    # 차단한다 (write path 원칙: 사용자는 marker를 쓸 수 없음).
    resp = await client.patch(
        f"/api/connections/{seeded_id}",
        json={"display_name": "[m10-auto-seed] naver-renamed"},
    )
    assert resp.status_code == 422
    assert "reserved marker" in resp.text or "[m10-auto-seed]" in resp.text

    # marker 없는 connection은 자유 rename (회귀 방지)
    other = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "google_search",
            "display_name": "My Google Key",
            "credential_id": None,
        },
    )
    assert other.status_code == 201
    resp = await client.patch(
        f"/api/connections/{other.json()['id']}",
        json={"display_name": "renamed freely"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_rejects_provider_mismatched_credential(
    client: AsyncClient, db: AsyncSession
):
    """PREBUILT connection에 provider_name이 다른 credential을 bind하면 400.

    허용되면 `_resolve_prebuilt_auth`가 엉뚱한 키로 auth_config를 만들고
    tool builder가 settings.* env로 조용히 떨어져 per-user 격리가 파손된다
    (Codex adversarial P1).
    """
    # naver credential 생성 (provider_name=naver)
    naver_cred = await _seed_credential(db, name="Naver cred")
    # google_search 타입의 connection에 naver credential 연결 시도 → 400
    resp = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "google_search",
            "display_name": "My Google Search",
            "credential_id": str(naver_cred.id),
        },
    )
    assert resp.status_code == 400
    assert "does not" in resp.json().get("detail", "")

    # 올바른 provider(naver)로는 성공
    resp = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "My Naver",
            "credential_id": str(naver_cred.id),
        },
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_patch_rejects_provider_mismatched_credential(
    client: AsyncClient, db: AsyncSession
):
    """PATCH로 credential_id 바꾸거나 provider_name 바꿀 때도 일치 검증."""
    naver_cred = await _seed_credential(db, name="Naver cred")
    # provider=naver로 먼저 만들고
    created = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "Naver conn",
            "credential_id": str(naver_cred.id),
        },
    )
    assert created.status_code == 201
    conn_id = created.json()["id"]

    # provider_name을 google_search로 바꾸려 하면 기존 naver credential과 불일치 → 400
    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={"provider_name": "google_search"},
    )
    assert resp.status_code == 400
    assert "does not" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# Extra — 알 수 없는 필드 전송 시 422 (extra="forbid")
# ---------------------------------------------------------------------------


def test_create_rejects_unknown_fields():
    """ConnectionCreate는 extra="forbid" — 정의되지 않은 필드 전송 시 422."""
    with pytest.raises(ValidationError) as exc_info:
        ConnectionCreate(
            type="prebuilt",
            provider_name="naver",
            display_name="N",
            unknown_field="nope",
        )
    assert "unknown_field" in str(exc_info.value)


def test_update_rejects_type_field():
    """ConnectionUpdate는 type 필드 자체를 정의하지 않고 extra="forbid"로 암묵 차단.
    type 변경 시도는 422."""
    from app.schemas.connection import ConnectionUpdate

    with pytest.raises(ValidationError) as exc_info:
        ConnectionUpdate(type="mcp")
    # Pydantic forbid 에러 메시지는 'Extra inputs are not permitted'
    assert "type" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Cross-tenant credential reference (P1-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_cross_tenant_credential(
    client: AsyncClient, db: AsyncSession
):
    """다른 유저의 credential_id로 connection을 만들 수 없다 (credential 소유권 검증)."""
    # OTHER_USER 소유의 credential 생성
    # OTHER_USER 존재 보장 (이미 다른 테스트가 만들어뒀을 수 있어 멱등 처리)
    existing = await db.get(User, OTHER_USER_ID)
    if existing is None:
        db.add(
            User(
                id=OTHER_USER_ID,
                email=f"other-{uuid.uuid4().hex[:8]}@test.com",
                name="Other User",
            )
        )
        await db.commit()
    other_cred = await _seed_credential(
        db, user_id=OTHER_USER_ID, name="OtherUserCred"
    )

    # 현재 유저(TEST_USER)가 타 유저의 cred_id를 참조 시도 → 404
    resp = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "X",
            "credential_id": str(other_cred.id),
        },
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_patch_rejects_cross_tenant_credential(
    client: AsyncClient, db: AsyncSession
):
    """PATCH로 타 유저의 credential_id를 설정할 수 없다."""
    # OTHER_USER 존재 보장 (이미 다른 테스트가 만들어뒀을 수 있어 멱등 처리)
    existing = await db.get(User, OTHER_USER_ID)
    if existing is None:
        db.add(
            User(
                id=OTHER_USER_ID,
                email=f"other-{uuid.uuid4().hex[:8]}@test.com",
                name="Other User",
            )
        )
        await db.commit()
    other_cred = await _seed_credential(
        db, user_id=OTHER_USER_ID, name="OtherUserCred"
    )

    created = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "mine",
        },
    )
    conn_id = created.json()["id"]

    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={"credential_id": str(other_cred.id)},
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Single-default invariant when provider_name changes (P1-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_provider_name_preserves_single_default_invariant(
    client: AsyncClient,
):
    """default 행의 provider_name을 이미 default가 있는 scope로 옮겨도
    해당 scope에는 default가 1개만 남아야 한다."""
    # scope A (naver)에 default 1개
    r_a = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "A-default",
        },
    )
    a_id = r_a.json()["id"]
    assert r_a.json()["is_default"] is True

    # scope B (google_search)에 default 1개
    r_b = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "google_search",
            "display_name": "B-default",
        },
    )
    b_id = r_b.json()["id"]
    assert r_b.json()["is_default"] is True

    # A를 B의 scope로 이동 — A는 is_default 그대로
    resp = await client.patch(
        f"/api/connections/{a_id}",
        json={"provider_name": "google_search"},
    )
    assert resp.status_code == 200
    assert resp.json()["provider_name"] == "google_search"
    assert resp.json()["is_default"] is True

    # google_search scope 목록 조회 — default는 딱 1개 (A)이고 B는 해제돼야 함
    lst = await client.get(
        "/api/connections",
        params={"type": "prebuilt", "provider_name": "google_search"},
    )
    rows = lst.json()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1, f"expected exactly 1 default, got {defaults}"
    assert defaults[0]["id"] == a_id
    # B는 default에서 내려와 있어야 함
    b_row = next(r for r in rows if r["id"] == b_id)
    assert b_row["is_default"] is False


# ---------------------------------------------------------------------------
# PATCH re-validation (P2) — POST가 거부할 상태를 PATCH로 만들 수 없음
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rejects_prebuilt_invalid_provider_name(
    client: AsyncClient,
):
    """type='prebuilt' 행의 provider_name을 non-enum으로 PATCH → 422."""
    created = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "N",
        },
    )
    conn_id = created.json()["id"]

    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={"provider_name": "not_in_registry"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_patch_rejects_mcp_extra_config_cleared(
    client: AsyncClient,
):
    """type='mcp' 행의 extra_config에서 url/auth_type을 제거하는 PATCH → 422."""
    created = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "my_mcp_server",
            "display_name": "M",
            "extra_config": {
                "url": "https://example.com/mcp",
                "auth_type": "none",
            },
        },
    )
    conn_id = created.json()["id"]

    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={"extra_config": {}},
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Default orphan 방지 — demote/rename-out/delete 후 대체 default 자동 승격
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_demote_default_promotes_replacement(
    client: AsyncClient,
):
    """같은 scope의 default를 `is_default=false`로 demote하면, scope에 다른 row가
    있을 때 가장 최근 row가 자동으로 default로 승격된다 (ADR-008 §5)."""
    # 첫 행: 자동 default
    r_a = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "A",
        },
    )
    a_id = r_a.json()["id"]
    assert r_a.json()["is_default"] is True

    # 둘째 행: is_default=False (명시)
    r_b = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "B",
            "is_default": False,
        },
    )
    b_id = r_b.json()["id"]
    assert r_b.json()["is_default"] is False

    # A를 demote → B가 자동 승격돼야 함
    resp = await client.patch(
        f"/api/connections/{a_id}",
        json={"is_default": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_default"] is False

    lst = await client.get(
        "/api/connections",
        params={"type": "prebuilt", "provider_name": "naver"},
    )
    rows = lst.json()
    defaults = [r for r in rows if r["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == b_id  # 가장 최근 row


@pytest.mark.asyncio
async def test_patch_rename_out_promotes_replacement_in_old_scope(
    client: AsyncClient,
):
    """default 행의 provider_name을 다른 scope로 바꿔 기존 scope를 떠나면,
    옛 scope의 남은 row 중 가장 최근 것이 default로 자동 승격된다."""
    # scope naver: A(default) + B
    r_a = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "A",
        },
    )
    a_id = r_a.json()["id"]
    r_b = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "B",
            "is_default": False,
        },
    )
    b_id = r_b.json()["id"]

    # A를 google_search scope로 이동
    resp = await client.patch(
        f"/api/connections/{a_id}",
        json={"provider_name": "google_search"},
    )
    assert resp.status_code == 200

    # 옛 scope(naver)에 B만 남음 → B가 default로 승격돼야
    lst = await client.get(
        "/api/connections",
        params={"type": "prebuilt", "provider_name": "naver"},
    )
    rows = lst.json()
    assert len(rows) == 1
    assert rows[0]["id"] == b_id
    assert rows[0]["is_default"] is True


@pytest.mark.asyncio
async def test_delete_default_promotes_replacement(
    client: AsyncClient,
):
    """default 행을 삭제하면, scope에 남은 row 중 가장 최근이 default로 승격된다."""
    r_a = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "A",
        },
    )
    a_id = r_a.json()["id"]
    r_b = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "B",
            "is_default": False,
        },
    )
    b_id = r_b.json()["id"]
    assert r_a.json()["is_default"] is True
    assert r_b.json()["is_default"] is False

    # A(default) 삭제
    resp = await client.delete(f"/api/connections/{a_id}")
    assert resp.status_code == 204

    # B가 자동 승격
    lst = await client.get(
        "/api/connections",
        params={"type": "prebuilt", "provider_name": "naver"},
    )
    rows = lst.json()
    assert len(rows) == 1
    assert rows[0]["id"] == b_id
    assert rows[0]["is_default"] is True


@pytest.mark.asyncio
async def test_patch_rename_nondefault_into_empty_scope_promotes_self(
    client: AsyncClient,
):
    """non-default row를 빈 scope로 rename하면 그 row가 새 scope의 default로
    자동 승격돼야 한다. (Codex 3차 리뷰 P2 — 양쪽 scope invariant 대칭)"""
    # scope naver: A(default) + B(non-default)
    await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "A",
        },
    )
    r_b = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "naver",
            "display_name": "B",
            "is_default": False,
        },
    )
    b_id = r_b.json()["id"]
    assert r_b.json()["is_default"] is False

    # B를 빈 scope(google_search)로 이동 — POST 첫 row 자동 default와 대칭
    resp = await client.patch(
        f"/api/connections/{b_id}",
        json={"provider_name": "google_search"},
    )
    assert resp.status_code == 200

    # google_search scope에 B가 유일하게 있고 default여야 함
    lst = await client.get(
        "/api/connections",
        params={"type": "prebuilt", "provider_name": "google_search"},
    )
    rows = lst.json()
    assert len(rows) == 1
    assert rows[0]["id"] == b_id
    assert rows[0]["is_default"] is True


# ---------------------------------------------------------------------------
# End-to-end HTTP 422 (main.py validation_error_handler 픽스 검증)
# Codex adversarial Finding 1 — field/model_validator ValueError가 실제로 422를
# 반환해야 하며 500이 나면 안 된다.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_returns_422_for_prebuilt_invalid_provider(
    client: AsyncClient,
):
    resp = await client.post(
        "/api/connections",
        json={
            "type": "prebuilt",
            "provider_name": "not_in_registry",
            "display_name": "X",
        },
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "details" in body["error"]


@pytest.mark.asyncio
async def test_api_returns_422_for_mcp_missing_extra_config(
    client: AsyncClient,
):
    resp = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "some_mcp_server",
            "display_name": "X",
            # extra_config 누락
        },
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_api_returns_422_for_mcp_extra_config_missing_url(
    client: AsyncClient,
):
    resp = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "some_mcp_server",
            "display_name": "X",
            "extra_config": {"auth_type": "bearer"},
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# env_vars 템플릿 검증 (Codex adversarial Finding 3)
# env_vars 값은 반드시 ${credential.<field>} 형태. 평문 문자열은 거부.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_vars_rejects_plaintext_value(client: AsyncClient):
    """env_vars에 평문 시크릿 값을 넣으려 하면 422."""
    resp = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "resend",
            "display_name": "Resend",
            "extra_config": {
                "url": "https://resend-mcp.example.com",
                "auth_type": "api_key",
                "env_vars": {"RESEND_API_KEY": "sk-live-real-secret-value"},
            },
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_env_vars_accepts_credential_template(client: AsyncClient):
    """env_vars 값이 ${credential.<field>} 템플릿이면 201."""
    resp = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "resend",
            "display_name": "Resend",
            "extra_config": {
                "url": "https://resend-mcp.example.com",
                "auth_type": "api_key",
                "env_vars": {"RESEND_API_KEY": "${credential.api_key}"},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Response side redacts env_vars values — only keys are exposed to the
    # client to prevent secret echo on read (Codex 4차 adversarial Finding).
    assert "env_vars" not in body["extra_config"]
    assert body["extra_config"]["env_var_keys"] == ["RESEND_API_KEY"]


@pytest.mark.asyncio
async def test_extra_config_rejects_unknown_keys(client: AsyncClient):
    """ConnectionExtraConfig는 extra='forbid' — url/auth_type 외 알 수 없는 키 거부."""
    resp = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "weird_mcp",
            "display_name": "W",
            "extra_config": {
                "url": "https://x",
                "auth_type": "none",
                "unknown_field": "nope",
            },
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Partial unique index enforcement (Codex adversarial Finding 2)
# DB 레벨 `uq_connections_one_default_per_scope` — 서비스 race 시 DB가 잡는다.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_unique_blocks_two_defaults_in_same_scope(
    db: AsyncSession,
):
    """두 connection을 같은 scope에 is_default=True로 직접 insert하면 DB가 거부한다.
    (서비스는 _clear_default_in_scope로 race를 완화하지만, DB 레벨 제약이 최종 안전망)"""
    from sqlalchemy.exc import IntegrityError as _IE

    c1 = Connection(
        user_id=TEST_USER_ID,
        type="prebuilt",
        provider_name="naver",
        display_name="A",
        is_default=True,
        status="active",
    )
    db.add(c1)
    await db.commit()

    c2 = Connection(
        user_id=TEST_USER_ID,
        type="prebuilt",
        provider_name="naver",
        display_name="B",
        is_default=True,
        status="active",
    )
    db.add(c2)
    with pytest.raises(_IE):
        await db.commit()
    await db.rollback()
