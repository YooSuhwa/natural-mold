"""PREBUILT → per-user Connection 이관 회귀 + 신규 테스트 (백로그 E M3).

ADR-008 §3/§4/§11 이행. PREBUILT 도구는 공유 행(`is_system=True, user_id=NULL`)이므로
runtime credential은 `tool.credential_id`가 아니라 호출 유저의 default Connection
(`user_id + type='prebuilt' + provider_name + is_default=true`)에서 꺼내야 한다.

본 파일은 **PREBUILT 분기 전용** (MCP는 별도 파일). M6 cleanup 전까지
`_resolve_prebuilt_auth(tool, map)` / `_resolve_legacy_tool_auth(tool)` 두 경로가
공존한다는 이행 tolerance를 시나리오로 고정한다:

1. **per-user 격리 (4-way cross-check)** — user_A agent는 A의 credential만 보고,
   user_B agent는 B의 credential만 본다. 섞이면 공유 행 뒤엉킴 regression.
2. **connection 부재 → env fallback** — `_resolve_prebuilt_auth`가 `{}` 반환해
   tool builder(naver_tools 등)가 `settings.*`로 회귀. bootstrap 경로.
2b. **disabled default → fail closed** — Codex adversarial P1. kill-switch가
    env fallback로 우회되지 않도록 ToolConfigError raise.
2c. **disabled → credential+status PATCH로 재활성화** — Codex adversarial P2.
3. **connection 있지만 credential NULL → fail closed** — unbound/삭제는 명시적
   의도로 간주, env fallback 거부.
4. **provider_name NULL PREBUILT → legacy 경로** — m10 백필 실패 row tolerance.
   M6에서 제거 예정.
5. **bulk N+1 방지** — 여러 PREBUILT tool을 건 agent가
   `_load_user_default_connection_map` 1회로 모두 로드.
6. **cross-tenant credential leak 가드** — connection.user_id == caller지만
   credential.user_id가 다르면 `ToolConfigError`.
7. **lifespan seed idempotent / race-safe** — 중복 insert 방지 + SAVEPOINT 래핑.
8. **m10 downgrade marker 정책** — `M10_SEED_MARKER`가 포함된 행만 역삭제하고
   user-created(마커 없음)는 보존한다.

m10 SQL은 `CAST(:field_keys AS JSON)` 등 PG 전용이라 aiosqlite에서 alembic을 직접
왕복할 수 없다. 시나리오 7-8은 helper/소스 레벨에서 invariant를 고정하고, PG 왕복은
통합 게이트(S6)에서 검증한다.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.connection import Connection
from app.models.credential import Credential
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from app.schemas.tool import ToolType
from app.services.chat_service import (
    _load_user_default_connection_map,
    _resolve_prebuilt_auth,
    build_tools_config,
    get_agent_with_tools,
)
from app.services.encryption import encrypt_api_key
from app.services.env_var_resolver import ToolConfigError
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# m10 모듈 로드 — alembic/ 디렉터리에는 __init__.py가 없어 importlib 사용
# ---------------------------------------------------------------------------


def _load_m10_module():
    repo_root = Path(__file__).resolve().parents[1]
    m10_path = repo_root / "alembic" / "versions" / "m10_prebuilt_connection_migration.py"
    spec = importlib.util.spec_from_file_location("_test_m10_prebuilt_connection", m10_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_m10 = _load_m10_module()


# ---------------------------------------------------------------------------
# 공용 fixture / helper
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """Fernet 키 주입 — credential 암호/복호 round-trip."""
    import app.services.encryption as enc_mod
    from app.config import settings

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", key, raising=False)
    original_fernet = enc_mod._fernet
    enc_mod._fernet = None
    yield
    enc_mod._fernet = original_fernet


async def _seed_user(db: AsyncSession, user_id: uuid.UUID, email: str) -> User:
    user = User(id=user_id, email=email, name=email.split("@")[0])
    db.add(user)
    await db.flush()
    return user


async def _seed_model(db: AsyncSession) -> Model:
    model = Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
    )
    db.add(model)
    await db.flush()
    return model


async def _seed_credential(
    db: AsyncSession,
    user_id: uuid.UUID,
    data: dict[str, str],
    *,
    name: str,
    provider_name: str,
) -> Credential:
    cred = Credential(
        user_id=user_id,
        name=name,
        credential_type="api_key",
        provider_name=provider_name,
        data_encrypted=encrypt_api_key(json.dumps(data)),
        field_keys=list(data.keys()),
    )
    db.add(cred)
    await db.flush()
    return cred


async def _seed_default_connection(
    db: AsyncSession,
    user_id: uuid.UUID,
    provider_name: str,
    credential: Credential | None,
) -> Connection:
    conn = Connection(
        user_id=user_id,
        type="prebuilt",
        provider_name=provider_name,
        display_name=f"{provider_name} default",
        credential_id=credential.id if credential else None,
        extra_config=None,
        is_default=True,
        status="active",
    )
    db.add(conn)
    await db.flush()
    return conn


async def _seed_prebuilt_tool(db: AsyncSession, *, name: str, provider_name: str | None) -> Tool:
    tool = Tool(
        user_id=None,  # PREBUILT 공유 행
        type=ToolType.PREBUILT,
        is_system=True,
        provider_name=provider_name,
        name=name,
        description=f"{name} (prebuilt)",
    )
    db.add(tool)
    await db.flush()
    return tool


async def _seed_agent_with_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
    model: Model,
    tools: list[Tool],
) -> Agent:
    agent = Agent(
        user_id=user_id,
        name="PREBUILT Agent",
        system_prompt="hi",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    for t in tools:
        db.add(AgentToolLink(agent_id=agent.id, tool_id=t.id))
    await db.commit()
    return agent


# ---------------------------------------------------------------------------
# Scenario 1 — per-user 격리 (4-way cross-check)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prebuilt_resolves_current_user_connection_not_other_user(
    db: AsyncSession,
):
    """user_A agent는 A의 Naver credential만, user_B agent는 B의 credential만.

    4-way cross-check: 정방향(A gets A) + 역방향(A NOT gets B) + 대칭
    (B gets B, B NOT gets A). ADR-008 §문제 1: PREBUILT shared row의 connection이
    `tool.connection_id` 기반이 아니라 `(user_id, provider_name)` scope로
    조회된다는 핵심 invariant.
    """
    user_a = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    user_b = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
    await _seed_user(db, user_a, "a@test.com")
    await _seed_user(db, user_b, "b@test.com")
    model = await _seed_model(db)

    cred_a = await _seed_credential(
        db,
        user_a,
        {"naver_client_id": "A_ID", "naver_client_secret": "A_SECRET"},
        name="A naver",
        provider_name="naver",
    )
    cred_b = await _seed_credential(
        db,
        user_b,
        {"naver_client_id": "B_ID", "naver_client_secret": "B_SECRET"},
        name="B naver",
        provider_name="naver",
    )

    conn_a = await _seed_default_connection(db, user_a, "naver", cred_a)
    conn_b = await _seed_default_connection(db, user_b, "naver", cred_b)

    naver_tool = await _seed_prebuilt_tool(db, name="Naver Blog Search", provider_name="naver")
    agent_a = await _seed_agent_with_tools(db, user_a, model, [naver_tool])
    agent_b = await _seed_agent_with_tools(db, user_b, model, [naver_tool])

    # --- agent_A: user_A 경로 ---
    loaded_a = await get_agent_with_tools(db, agent_a.id, user_a)
    assert loaded_a is not None
    # user_id 필터 검증: default_connection_map의 connection은 user_A 소유
    conn_map_a = loaded_a._default_connection_map  # type: ignore[attr-defined]
    assert "naver" in conn_map_a
    assert conn_map_a["naver"].id == conn_a.id
    assert conn_map_a["naver"].user_id == user_a
    assert conn_map_a["naver"].id != conn_b.id  # 역방향

    cfg_a = build_tools_config(loaded_a)[0]
    assert cfg_a["auth_config"] == {
        "naver_client_id": "A_ID",
        "naver_client_secret": "A_SECRET",
    }
    # 역방향: B의 값이 절대 섞이면 안 됨
    assert cfg_a["auth_config"]["naver_client_id"] != "B_ID"
    assert cfg_a["auth_config"]["naver_client_secret"] != "B_SECRET"

    # --- agent_B: user_B 경로 (대칭 검증) ---
    loaded_b = await get_agent_with_tools(db, agent_b.id, user_b)
    assert loaded_b is not None
    conn_map_b = loaded_b._default_connection_map  # type: ignore[attr-defined]
    assert conn_map_b["naver"].id == conn_b.id
    assert conn_map_b["naver"].user_id == user_b
    assert conn_map_b["naver"].id != conn_a.id

    cfg_b = build_tools_config(loaded_b)[0]
    assert cfg_b["auth_config"] == {
        "naver_client_id": "B_ID",
        "naver_client_secret": "B_SECRET",
    }
    assert cfg_b["auth_config"]["naver_client_id"] != "A_ID"


# ---------------------------------------------------------------------------
# Scenario 2 — connection 부재 → env fallback (empty auth_config)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prebuilt_without_default_connection_returns_empty_auth(
    db: AsyncSession,
):
    """default connection이 없으면 `_resolve_prebuilt_auth`는 `{}`를 반환해
    tool builder(naver_tools / google_tools)가 `settings.*` env fallback을
    적용하게 한다 (ADR-008 §11). 런타임이 500으로 죽으면 안 됨.

    `auth_config or None` 직렬화 규칙 때문에 최종 config_entry에는 `auth_config:
    None`이 들어간다 — tool builder가 이 시그널로 env fallback 경로에 진입.
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    # connection 시드 생략 — scope가 비어 있는 상태
    naver_tool = await _seed_prebuilt_tool(db, name="Naver News Search", provider_name="naver")
    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, [naver_tool])

    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None
    # map은 provider_name 수집은 하지만 매치되는 row가 없어 빈 dict
    assert loaded._default_connection_map == {}  # type: ignore[attr-defined]

    cfg = build_tools_config(loaded)[0]
    # merged_auth = {**{}, **None} = {} → `auth_config or None` → None
    assert cfg["auth_config"] is None
    assert cfg["type"] == "prebuilt"
    assert cfg["name"] == "Naver News Search"


# ---------------------------------------------------------------------------
# Scenario 2b — disabled connection은 런타임 resolution에서 제외 (kill switch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_default_connection_fails_closed(
    db: AsyncSession,
):
    """`status='disabled'` connection은 execution을 차단해야 한다 (fail closed).

    이전(2차 adversarial)에는 status='active' 필터로 map에서 제외 → env
    fallback으로 떨어졌으나, Codex 4차 지적: env fallback이 kill-switch를
    무력화한다. 3-state 도입 후: map에는 disabled도 포함되고, 런타임에서
    `ToolConfigError`를 raise해 공용 env secret으로의 우회를 차단한다.
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    cred = await _seed_credential(
        db,
        TEST_USER_ID,
        {"naver_client_id": "A_ID", "naver_client_secret": "A_SECRET"},
        name="Naver cred",
        provider_name="naver",
    )
    conn = Connection(
        user_id=TEST_USER_ID,
        type="prebuilt",
        provider_name="naver",
        display_name="naver disabled",
        credential_id=cred.id,
        extra_config=None,
        is_default=True,
        status="disabled",
    )
    db.add(conn)
    await db.flush()

    naver_tool = await _seed_prebuilt_tool(db, name="Naver News Search", provider_name="naver")
    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, [naver_tool])

    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None
    # disabled도 map에 포함 — 런타임 resolver가 3-state로 판단해야 함
    m = loaded._default_connection_map  # type: ignore[attr-defined]
    assert "naver" in m
    assert m["naver"].status == "disabled"

    # build_tools_config → _resolve_prebuilt_auth → ToolConfigError
    with pytest.raises(ToolConfigError) as exc_info:
        build_tools_config(loaded)
    assert "status='disabled'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unbound_default_connection_fails_closed(
    db: AsyncSession,
):
    """credential_id=NULL(명시적 unbind 또는 ON DELETE SET NULL) connection은
    execution을 차단해야 한다. env fallback으로 우회하면 kill-switch가 무력화
    된다 (Codex adversarial 4차 P1).
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    conn = Connection(
        user_id=TEST_USER_ID,
        type="prebuilt",
        provider_name="naver",
        display_name="naver unbound",
        credential_id=None,
        extra_config=None,
        is_default=True,
        status="active",
    )
    db.add(conn)
    await db.flush()

    naver_tool = await _seed_prebuilt_tool(db, name="Naver News Search", provider_name="naver")
    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, [naver_tool])
    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None

    with pytest.raises(ToolConfigError) as exc_info:
        build_tools_config(loaded)
    assert "no bound credential" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Scenario 2c — disabled default는 credential+status PATCH로 재활성화 가능
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_default_reactivates_via_credential_and_status_patch(
    db: AsyncSession,
):
    """Frontend binding dialog가 `credential_id` + `status='active'`를 함께
    PATCH하면 disabled default connection이 재활성화되어 런타임 resolution에
    다시 등장해야 한다 (Codex adversarial P2).

    이 테스트는 백엔드 contract만 검증 — 실제 프론트 handleSave의 payload
    형상은 빌드/타입 레벨에서 강제된다 (ConnectionUpdateRequest.status).
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    cred = await _seed_credential(
        db,
        TEST_USER_ID,
        {"naver_client_id": "A_ID", "naver_client_secret": "A_SECRET"},
        name="Naver cred",
        provider_name="naver",
    )
    conn = Connection(
        user_id=TEST_USER_ID,
        type="prebuilt",
        provider_name="naver",
        display_name="naver disabled",
        credential_id=cred.id,
        extra_config=None,
        is_default=True,
        status="disabled",
    )
    db.add(conn)
    await db.flush()

    # 프론트가 전송하는 payload 동등 — credential_id + status='active'
    from app.schemas.connection import ConnectionUpdate
    from app.services.connection_service import update_connection

    updated = await update_connection(
        db,
        conn.id,
        TEST_USER_ID,
        ConnectionUpdate(credential_id=cred.id, status="active"),
    )
    assert updated is not None
    assert updated.status == "active"

    # 재활성화 후 runtime resolution에 다시 등장
    naver_tool = await _seed_prebuilt_tool(db, name="Naver News Search", provider_name="naver")
    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, [naver_tool])
    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None
    m = loaded._default_connection_map  # type: ignore[attr-defined]
    assert "naver" in m
    assert m["naver"].credential_id == cred.id


# ---------------------------------------------------------------------------
# Scenario 3 — connection 있지만 credential NULL (ON DELETE SET NULL)
# ---------------------------------------------------------------------------


def test_prebuilt_connection_with_dangling_credential_fails_closed():
    """Credential이 삭제되어 connection.credential_id가 ON DELETE SET NULL로
    끊긴 dangling 상태: `_resolve_prebuilt_auth`는 `ToolConfigError`를 raise
    해 tool 실행을 차단해야 한다.

    이전에는 `{}`를 반환해 env fallback으로 회귀했으나, Codex 4차 지적:
    "connection은 있지만 credential 없음"은 명시적 unbind 의도와 구분
    불가능하고 env fallback은 kill-switch 무력화. fail closed로 전환.
    단위 테스트 — connection_map을 in-memory로 구성해 헬퍼만 호출한다.
    """
    tool = Tool(
        user_id=None,
        type=ToolType.PREBUILT,
        is_system=True,
        provider_name="naver",
        name="Naver Blog Search",
    )
    dangling = Connection(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        type="prebuilt",
        provider_name="naver",
        display_name="Naver (dangling)",
        credential_id=None,  # FK SET NULL 결과 또는 사용자 명시적 unbind
        credential=None,
        is_default=True,
        status="active",
    )
    with pytest.raises(ToolConfigError) as exc_info:
        _resolve_prebuilt_auth(tool, {"naver": dangling})
    assert "no bound credential" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Scenario 4 — provider_name NULL PREBUILT → empty auth (M6 이후)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prebuilt_without_provider_name_returns_empty_auth(
    db: AsyncSession,
):
    """m10 백필 실패 row(`provider_name IS NULL`)는 M6 이후 empty auth
    (`{}`)로 처리된다 — legacy `tools.credential_id`/`auth_config` 컬럼이
    drop 되었으므로 더 이상 inline fallback 할 값이 없다. env fallback 과
    동치.
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    legacy_tool = Tool(
        user_id=None,
        type=ToolType.PREBUILT,
        is_system=True,
        provider_name=None,
        name="Unmapped Legacy Prebuilt",
        description="m10 backfill miss",
    )
    db.add(legacy_tool)
    await db.flush()

    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, [legacy_tool])
    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None

    cfg = build_tools_config(loaded)[0]
    assert cfg["auth_config"] is None


# ---------------------------------------------------------------------------
# Scenario 5 — bulk N+1 방지: 3 PREBUILT provider를 1 쿼리로 로드
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_connection_map_uses_single_bulk_query(
    db: AsyncSession,
):
    """한 agent에 naver + google_search + google_workspace PREBUILT 3종이 붙어
    있어도 `_load_user_default_connection_map`은 IN 쿼리 1회로 끝나야 한다.
    provider당 개별 쿼리로 회귀하면 에이전트 tool 수만큼 DB 왕복이 늘어
    chat 응답 latency가 O(n)로 증가 (ADR-008 §3 비용 제약).
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    provider_to_data = {
        "naver": {
            "naver_client_id": "N_ID",
            "naver_client_secret": "N_SECRET",
        },
        "google_search": {
            "google_api_key": "G_KEY",
            "google_cse_id": "G_CSE",
        },
        "google_workspace": {
            "google_oauth_client_id": "W_ID",
            "google_oauth_client_secret": "W_SECRET",
            "google_oauth_refresh_token": "W_RT",
        },
    }

    tools: list[Tool] = []
    for provider_name, data in provider_to_data.items():
        cred = await _seed_credential(
            db,
            TEST_USER_ID,
            data,
            name=f"{provider_name} cred",
            provider_name=provider_name,
        )
        await _seed_default_connection(db, TEST_USER_ID, provider_name, cred)
        tool = await _seed_prebuilt_tool(
            db, name=f"{provider_name} tool", provider_name=provider_name
        )
        tools.append(tool)

    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, tools)

    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None

    # 쿼리 카운트: `_load_user_default_connection_map` 호출 시 connections IN 1건.
    # selectinload(credential) 때문에 credentials 로딩은 selectin이 추가되지만,
    # Connection 테이블 자체에 대한 쿼리는 정확히 1회여야 한다.
    engine = db.bind  # AsyncSession의 바인딩된 async engine
    sync_engine = engine.sync_engine  # type: ignore[union-attr]
    queries: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        conn_map = await _load_user_default_connection_map(db, loaded, TEST_USER_ID)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _before_cursor_execute)

    connection_select_count = sum(
        1
        for q in queries
        if q.lstrip().upper().startswith("SELECT")
        and "connections" in q.lower()
        and " in (" in q.lower()
    )
    assert connection_select_count == 1, (
        f"expected exactly 1 bulk SELECT on connections, got {connection_select_count}\n"
        + "\n".join(queries)
    )

    # 모든 provider가 한 번에 로드됨
    assert set(conn_map.keys()) == set(provider_to_data.keys())

    # 실제 build_tools_config 출력도 각 provider별 credential로 치환됨
    configs = {c["name"]: c for c in build_tools_config(loaded)}
    assert configs["naver tool"]["auth_config"] == provider_to_data["naver"]
    assert configs["google_search tool"]["auth_config"] == provider_to_data["google_search"]
    assert configs["google_workspace tool"]["auth_config"] == provider_to_data["google_workspace"]


# ---------------------------------------------------------------------------
# Scenario 6 — cross-tenant credential leak 가드
# ---------------------------------------------------------------------------


def test_prebuilt_rejects_connection_credential_user_mismatch():
    """`_resolve_prebuilt_auth`는 connection.user_id == caller라도 credential이
    타 유저 소유면 `ToolConfigError`로 거부한다(env_var_resolver.assert_credential_ownership).

    PREBUILT tool은 `user_id=NULL` 공유 행이므로 tool↔connection ownership
    비교 자체는 no-op이지만, 그 뒤에 오는 connection↔credential ownership은
    실질 가드다. DML/M6 마이그레이션 실수로 credential_user_id가 어긋나도
    복호화 단계에서 즉시 막는다 (M2에서 확립된 런타임 방어선 재사용).
    """
    caller = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    other = uuid.UUID("00000000-0000-0000-0000-0000000000bb")

    tool = Tool(
        user_id=None,
        type=ToolType.PREBUILT,
        is_system=True,
        provider_name="naver",
        name="Naver Shop Search",
    )
    foreign_cred = Credential(
        id=uuid.uuid4(),
        user_id=other,  # 타 유저 소유
        name="foreign",
        credential_type="api_key",
        provider_name="naver",
        data_encrypted="x",
        field_keys=["naver_client_id"],
    )
    conn = Connection(
        id=uuid.uuid4(),
        user_id=caller,  # caller와 일치하지만
        type="prebuilt",
        provider_name="naver",
        display_name="Naver (leaked cred)",
        credential_id=foreign_cred.id,
        credential=foreign_cred,  # credential만 타 유저
        is_default=True,
        status="active",
    )
    with pytest.raises(ToolConfigError, match="owned by a different user"):
        _resolve_prebuilt_auth(tool, {"naver": conn})


# ---------------------------------------------------------------------------
# Scenario 7 — m10 idempotent 시드 (기존 default connection이 있으면 skip)
# ---------------------------------------------------------------------------


def test_lifespan_seed_is_idempotent_source_contract():
    """`seed_mock_user_prebuilt_connections`가 동일 scope default connection이 이미
    있으면 skip (continue)하는지 source-level로 가드.

    실제 SQL 왕복은 PG 전용(`CAST(:field_keys AS JSON)`)이라 aiosqlite에서
    실행 불가 — 소스 contract로 invariant를 고정하고, 런타임 왕복은 통합
    게이트(S6)에서 PG로 검증한다. 위반 시 partial unique index가 매 기동에서
    IntegrityError를 던져 lifespan seed가 실패한다.
    """
    import inspect

    from app.seed import prebuilt_connections as seed_mod

    src = inspect.getsource(seed_mod.seed_mock_user_prebuilt_connections)
    # 존재 체크 SELECT가 있어야 함
    assert "SELECT 1 FROM connections" in src
    # is_default = TRUE 기준으로 중복 탐지
    assert "is_default = TRUE" in src
    # ENCRYPTION_KEY 가드 존재 확인
    assert "encryption_key" in src


def test_lifespan_seed_wraps_inserts_in_savepoint_for_race_safety():
    """Concurrent boot race 방지 — INSERT 쌍이 SAVEPOINT(`begin_nested`) 안에서
    실행되고 IntegrityError를 catch해 graceful skip하는지 source-level 가드.

    위반 시 두 pod 동시 기동 race에서 loser pod의 lifespan이 abort되어
    CrashLoopBackOff 발생 (Codex adversarial 3차 P1).
    """
    import inspect

    from app.seed import prebuilt_connections as seed_mod

    src = inspect.getsource(seed_mod.seed_mock_user_prebuilt_connections)
    # SAVEPOINT 래핑
    assert "begin_nested" in src
    # IntegrityError catch 존재
    assert "IntegrityError" in src
    # credential/connection INSERT가 같은 savepoint 블록 안에 있어야 orphan 방지
    # (단순 검증 — 두 INSERT가 try 블록 밖에 있지 않음)
    try_idx = src.index("try:")
    nested_idx = src.index("begin_nested", try_idx)
    cred_insert_idx = src.index("INSERT INTO credentials", nested_idx)
    conn_insert_idx = src.index("INSERT INTO connections", cred_insert_idx)
    except_idx = src.index("except IntegrityError", conn_insert_idx)
    assert nested_idx < cred_insert_idx < conn_insert_idx < except_idx


def test_lifespan_seed_provider_mapping_covers_m3_scope():
    """`PROVIDERS`가 M3 스코프 4종(naver/google_search/google_chat/
    google_workspace)을 전부 커버하는지 가드. 빠지면 lifespan seed가 조용히
    건너뛰어 사용자는 ``settings.*`` env 있는데도 UI에서 "미연결"로 보인다.
    """
    from app.seed.prebuilt_connections import PROVIDERS

    providers = {p["provider_name"] for p in PROVIDERS}
    assert providers == {
        "naver",
        "google_search",
        "google_chat",
        "google_workspace",
    }


def test_lifespan_seed_derives_from_credential_registry():
    """seed `PROVIDERS`는 `CREDENTIAL_PROVIDERS`에서 `env_field`가 모두 설정된
    provider만 파생해야 한다. 싱글 소스 구조를 고정 — registry 필드가 변해도
    seed가 자동 동기화되므로 env→data key mismatch(Codex review P2 재발)가
    컴파일 타임에 차단된다.
    """
    from app.seed.prebuilt_connections import PROVIDERS
    from app.services.credential_registry import CREDENTIAL_PROVIDERS

    for entry in PROVIDERS:
        provider = entry["provider_name"]
        registry_fields = CREDENTIAL_PROVIDERS[provider]["fields"]
        # 모든 field에 env_field가 있어야 seed 대상
        assert all(f.get("env_field") for f in registry_fields)
        # env_to_key 매핑이 registry의 (env_field → key) 매핑과 정확히 일치
        expected = {f["env_field"]: f["key"] for f in registry_fields}
        assert entry["env_to_key"] == expected


# ---------------------------------------------------------------------------
# Scenario 8 — m10 downgrade marker 정책
# ---------------------------------------------------------------------------


def test_m10_downgrade_only_deletes_seed_marker_rows():
    """`M10_SEED_MARKER`가 포함된 connection/credential만 역삭제하고 UI로
    만든 행은 보존해야 한다. downgrade가 marker 없는 행까지 지우면 운영
    중 rollback 시 유저 데이터 손실.

    source-level 가드 — 실제 SQL은 `LIKE :marker` 패턴으로 제한적 DELETE.
    """
    import inspect

    from app.schemas.markers import M10_SEED_MARKER

    assert _m10.M10_SEED_MARKER == M10_SEED_MARKER == "[m10-auto-seed]"

    down_src = inspect.getsource(_m10.downgrade)
    # connections/credentials 두 테이블 모두 marker LIKE 패턴으로 제한
    assert "display_name LIKE :m" in down_src
    assert "name LIKE :m" in down_src
    # WHERE 없는 일괄 DELETE가 있으면 안 됨
    assert "DELETE FROM connections\n" not in down_src.replace("  ", "")
    assert "DELETE FROM credentials\n" not in down_src.replace("  ", "")

    # lifespan seed에서도 실제로 marker를 붙이는지 확인
    from app.seed import prebuilt_connections as seed_mod

    seed_src = inspect.getsource(seed_mod.seed_mock_user_prebuilt_connections)
    assert "M10_SEED_MARKER" in seed_src
    assert "credential_name" in seed_src
    assert "connection_display" in seed_src
