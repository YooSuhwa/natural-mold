"""CUSTOM → Connection 이관 회귀 + 신규 테스트 (백로그 E M4).

ADR-008 §4 M4 이행. CUSTOM 도구는 기존 `tool.credential_id` 직접 참조 경로에서
`tool.connection_id → connection.credential_id` 경유로 이관된다. PREBUILT와
달리 CUSTOM tool은 `user_id NOT NULL`(공유 행 아님)이고 **env fallback이 없다**
— `connection_id IS NULL` 시에는 legacy 경로(`_resolve_legacy_tool_auth`)가
M6까지 tolerance로 유지된다.

본 파일은 **CUSTOM 분기 전용**. PREBUILT는 `test_connection_prebuilt_resolve.py`,
MCP는 별도. `_resolve_custom_auth(tool) -> dict`의 3-state fail-closed 정책을
시나리오 1:1로 고정한다:

1. **per-user 격리 (4-way cross-check)** — CUSTOM은 tool 단위 user_id라 각
   user가 자기 tool/connection/credential을 따로 가진다. 4-way로 cross-check.
2. **connection_id + active + credential → 정상 복호화** — `_resolve_custom_auth`의
   State 3 happy path.
3a. **connection_id + disabled → ToolConfigError** — fail closed. PREBUILT와 달리
    CUSTOM은 env fallback 자체가 없어 legacy로도 회귀하지 않는다.
3b. **connection_id + credential=NULL → ToolConfigError** — dangling credential도
    명시적 unbind 의도로 간주, 실행 차단.
4. **connection_id=NULL + credential_id → legacy 경로** — m11 backfill 전 row
   tolerance. `_resolve_legacy_tool_auth`로 위임되어 credential 복호화.
5. **connection_id=NULL + credential_id=NULL + auth_config → inline auth** —
   legacy 경로의 두 번째 우선순위. M6에서 제거 예정.
6. **ownership mismatch (connection.user_id ≠ credential.user_id)** — runtime
   `assert_credential_ownership`가 credential leak을 거부.
7. **m11 upgrade source contract** — `_migrate_custom_credentials`의 대상 SQL /
   dedup / marker / `uq_connections_one_default_per_scope` race 가드.
8. **m11 downgrade source contract** — 마커 제한 DELETE + tool.connection_id NULL
   역설정 + 수동 생성 CUSTOM connection 보존.

m11 SQL은 PG 전용 네이티브(`CAST ... AS JSON` 없지만 `UPDATE ... IN (SELECT ...)`
포함)라 aiosqlite에서 alembic을 직접 왕복하지 않는다. 시나리오 7-8은 helper의
소스 계약을 `inspect.getsource`로 고정하고, 실제 왕복은 통합 게이트(S6)에서 PG로
검증한다 (M9/M10/M11 precedent).
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.connection import Connection
from app.models.credential import Credential
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from app.schemas.tool import ToolType
from app.services.chat_service import (
    _resolve_custom_auth,
    build_tools_config,
    get_agent_with_tools,
)
from app.services.encryption import encrypt_api_key
from app.services.env_var_resolver import ToolConfigError
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# m11 모듈 로드 — alembic/ 디렉터리에는 __init__.py가 없어 importlib 사용
# (PREBUILT 테스트의 m10 로드 패턴 재사용)
# ---------------------------------------------------------------------------


def _load_m11_module():
    repo_root = Path(__file__).resolve().parents[1]
    m11_path = repo_root / "alembic" / "versions" / "m11_custom_credential_migration.py"
    spec = importlib.util.spec_from_file_location("_test_m11_custom_connection", m11_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_m11 = _load_m11_module()


# ---------------------------------------------------------------------------
# 공용 fixture / helper (PREBUILT 테스트 helper의 CUSTOM 변형)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
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
) -> Credential:
    cred = Credential(
        user_id=user_id,
        name=name,
        credential_type="api_key",
        provider_name="custom_api_key",
        data_encrypted=encrypt_api_key(json.dumps(data)),
        field_keys=list(data.keys()),
    )
    db.add(cred)
    await db.flush()
    return cred


async def _seed_custom_connection(
    db: AsyncSession,
    user_id: uuid.UUID,
    credential: Credential | None,
    *,
    display_name: str = "custom conn",
    status: str = "active",
    is_default: bool = True,
) -> Connection:
    conn = Connection(
        user_id=user_id,
        type="custom",
        provider_name="custom_api_key",
        display_name=display_name,
        credential_id=credential.id if credential else None,
        extra_config=None,
        is_default=is_default,
        status=status,
    )
    db.add(conn)
    await db.flush()
    return conn


async def _seed_custom_tool(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    connection: Connection | None = None,
) -> Tool:
    tool = Tool(
        user_id=user_id,
        type=ToolType.CUSTOM,
        is_system=False,
        name=name,
        description=f"{name} (custom)",
        api_url="https://api.example.com/endpoint",
        http_method="GET",
        connection_id=connection.id if connection else None,
    )
    db.add(tool)
    await db.flush()
    return tool


async def _seed_agent_with_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
    model: Model,
    tools: list[Tool],
    *,
    name: str = "CUSTOM Agent",
) -> Agent:
    agent = Agent(
        user_id=user_id,
        name=name,
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
async def test_custom_resolves_current_user_connection_not_other_user(
    db: AsyncSession,
):
    """user_A와 user_B가 각자의 custom tool + connection + credential을 가질 때
    build_tools_config가 각 유저의 credential만 복호화해야 한다.

    CUSTOM은 tool 단위 user_id라 공유 행 회귀(PREBUILT §문제 1)와는 다른 결의
    격리 검증 — tool row 자체가 user별로 갈라지므로 실제 회귀 위험은 "동일 user가
    타 user credential을 참조하는 connection에 바인딩된 tool을 갖는" 경우(S6
    `assert_credential_ownership`에서 커버)지만, 여기서는 4-way cross-check로
    기본 happy path의 교차 오염을 방지한다.
    """
    user_a = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    user_b = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
    await _seed_user(db, user_a, "a@test.com")
    await _seed_user(db, user_b, "b@test.com")
    model = await _seed_model(db)

    cred_a = await _seed_credential(
        db, user_a, {"api_key": "A_KEY", "api_secret": "A_SECRET"}, name="A cred"
    )
    cred_b = await _seed_credential(
        db, user_b, {"api_key": "B_KEY", "api_secret": "B_SECRET"}, name="B cred"
    )
    conn_a = await _seed_custom_connection(db, user_a, cred_a, display_name="A conn")
    conn_b = await _seed_custom_connection(db, user_b, cred_b, display_name="B conn")
    tool_a = await _seed_custom_tool(db, user_id=user_a, name="A Tool", connection=conn_a)
    tool_b = await _seed_custom_tool(db, user_id=user_b, name="B Tool", connection=conn_b)

    agent_a = await _seed_agent_with_tools(db, user_a, model, [tool_a], name="A")
    agent_b = await _seed_agent_with_tools(db, user_b, model, [tool_b], name="B")

    # --- agent_A: user_A의 credential만 보이고 B는 섞이지 않음 ---
    loaded_a = await get_agent_with_tools(db, agent_a.id, user_a)
    assert loaded_a is not None
    cfg_a = build_tools_config(loaded_a)[0]
    assert cfg_a["auth_config"] == {"api_key": "A_KEY", "api_secret": "A_SECRET"}
    assert cfg_a["auth_config"]["api_key"] != "B_KEY"
    assert cfg_a["auth_config"]["api_secret"] != "B_SECRET"
    assert cfg_a["type"] == "custom"
    assert cfg_a["name"] == "A Tool"

    # --- agent_B: 대칭 검증 ---
    loaded_b = await get_agent_with_tools(db, agent_b.id, user_b)
    assert loaded_b is not None
    cfg_b = build_tools_config(loaded_b)[0]
    assert cfg_b["auth_config"] == {"api_key": "B_KEY", "api_secret": "B_SECRET"}
    assert cfg_b["auth_config"]["api_key"] != "A_KEY"


# ---------------------------------------------------------------------------
# Scenario 2 — connection_id + active + credential → 정상 복호화
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_with_active_connection_resolves_credential(
    db: AsyncSession,
):
    """connection_id FK가 있고 connection.status='active' + credential 존재 →
    `resolve_credential_data(conn.credential)` 경로로 정상 복호화.
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    cred = await _seed_credential(
        db,
        TEST_USER_ID,
        {"api_key": "active-key", "api_secret": "active-secret"},
        name="active cred",
    )
    conn = await _seed_custom_connection(db, TEST_USER_ID, cred)
    tool = await _seed_custom_tool(db, user_id=TEST_USER_ID, name="Active Tool", connection=conn)
    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, [tool])

    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None
    cfg = build_tools_config(loaded)[0]
    assert cfg["auth_config"] == {
        "api_key": "active-key",
        "api_secret": "active-secret",
    }
    # connection_id가 tool entry에 드러나지는 않지만 cred 값이 정확하면 경로는 통과
    assert cfg["type"] == "custom"
    assert cfg["api_url"] == "https://api.example.com/endpoint"


# ---------------------------------------------------------------------------
# Scenario 3a — connection_id + disabled → ToolConfigError (fail closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_disabled_connection_fails_closed(db: AsyncSession):
    """`status='disabled'` connection은 실행을 차단해야 한다. CUSTOM은 PREBUILT와
    달리 env fallback이 없으므로 여기서도 `_resolve_legacy_tool_auth`로 회귀하지
    않는다 — `connection_id IS NOT NULL`이면 kill-switch가 우선.
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    cred = await _seed_credential(
        db, TEST_USER_ID, {"api_key": "DISABLED_KEY"}, name="disabled cred"
    )
    conn = await _seed_custom_connection(
        db, TEST_USER_ID, cred, display_name="disabled conn", status="disabled"
    )
    tool = await _seed_custom_tool(db, user_id=TEST_USER_ID, name="Disabled Tool", connection=conn)
    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, [tool])

    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None
    with pytest.raises(ToolConfigError) as exc_info:
        build_tools_config(loaded)
    assert "status='disabled'" in str(exc_info.value)
    assert "Tool 'Disabled Tool'" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Scenario 3b — connection_id + credential=NULL → ToolConfigError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_connection_with_null_credential_fails_closed(
    db: AsyncSession,
):
    """credential_id=NULL(명시적 unbind 또는 ON DELETE SET NULL) connection은
    실행을 차단해야 한다. PREBUILT와 동일한 fail-closed 정책.
    """
    await _seed_user(db, TEST_USER_ID, "test@test.com")
    model = await _seed_model(db)

    conn = await _seed_custom_connection(
        db,
        TEST_USER_ID,
        None,
        display_name="unbound conn",
        status="active",
    )
    tool = await _seed_custom_tool(db, user_id=TEST_USER_ID, name="Unbound Tool", connection=conn)
    agent = await _seed_agent_with_tools(db, TEST_USER_ID, model, [tool])

    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None
    with pytest.raises(ToolConfigError) as exc_info:
        build_tools_config(loaded)
    assert "no bound credential" in str(exc_info.value)
    assert "Tool 'Unbound Tool'" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Scenario 3c — connection_id IS NOT NULL AND connection IS NULL → fail-closed
# (FK dangling or caller contract violation — must not silently legacy-fallback)
# ---------------------------------------------------------------------------


def test_custom_resolves_raises_when_connection_missing_despite_fk():
    """연결 FK(`tool.connection_id`)가 설정되어 있는데 relationship이 None인
    상태(예: 원본 connection row가 삭제됐거나 호출자가 eager-load를 걸지 않음)는
    legacy fallback으로 회귀하면 안 된다 — 사용자가 UI에서 '연결됨'으로 본
    상태가 무시되고 kill-switch가 우회될 수 있다. `ToolConfigError` fail-closed.
    """
    from unittest.mock import MagicMock

    tool = MagicMock(spec=Tool)
    tool.type = ToolType.CUSTOM
    tool.name = "Ghost Connection Tool"
    tool.connection_id = uuid.uuid4()
    tool.connection = None  # FK 있는데 relationship None
    tool.user_id = TEST_USER_ID

    with pytest.raises(ToolConfigError) as exc_info:
        _resolve_custom_auth(tool)
    assert "connection relationship is missing" in str(exc_info.value)
    assert "Ghost Connection Tool" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Scenario 4 — connection_id=NULL → ToolConfigError (fail-closed, M6 이후)
# ---------------------------------------------------------------------------


def test_custom_resolves_raises_when_connection_id_is_null():
    """M6 cleanup 이후 legacy credential/auth_config 필드가 drop 되었으므로
    connection_id=NULL 인 CUSTOM tool은 fail-closed. M6 이전에는
    `_resolve_legacy_tool_auth` 로 위임되어 tool.credential/auth_config 를
    fallback 으로 사용했지만 M6 에서는 필드 자체가 사라진다.
    """
    tool = Tool(
        user_id=TEST_USER_ID,
        type=ToolType.CUSTOM,
        is_system=False,
        name="Unbound Custom Tool",
        connection_id=None,
    )
    with pytest.raises(ToolConfigError) as exc_info:
        _resolve_custom_auth(tool)
    assert "no connection_id" in str(exc_info.value)
    assert "Unbound Custom Tool" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Scenario 6 — ownership mismatch: connection.user_id ≠ credential.user_id
# ---------------------------------------------------------------------------


def test_custom_rejects_connection_credential_user_mismatch():
    """connection.user_id == tool.user_id지만 credential.user_id가 다르면
    `assert_credential_ownership`가 `ToolConfigError`로 차단. M2 방어선을
    CUSTOM 경로에서 재사용하는지 고정한다.

    CUSTOM은 `tool.user_id NOT NULL`(공유 행이 아님)이라 tool↔connection
    ownership assertion도 실질 guard. connection↔credential 쌍도 별도 assertion.
    단위 테스트 — `_resolve_custom_auth`를 in-memory tool로 직접 호출.
    """
    caller = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    other = uuid.UUID("00000000-0000-0000-0000-0000000000bb")

    foreign_cred = Credential(
        id=uuid.uuid4(),
        user_id=other,  # 타 유저 소유
        name="foreign cred",
        credential_type="api_key",
        provider_name="custom_api_key",
        data_encrypted="x",
        field_keys=["api_key"],
    )
    conn = Connection(
        id=uuid.uuid4(),
        user_id=caller,
        type="custom",
        provider_name="custom_api_key",
        display_name="caller conn (leaked cred)",
        credential_id=foreign_cred.id,
        credential=foreign_cred,
        is_default=True,
        status="active",
    )
    tool = Tool(
        user_id=caller,
        type=ToolType.CUSTOM,
        is_system=False,
        name="Leaky Tool",
        connection_id=conn.id,
        connection=conn,
    )
    with pytest.raises(ToolConfigError, match="owned by a different user"):
        _resolve_custom_auth(tool)


# ---------------------------------------------------------------------------
# Scenario 7 — m11 upgrade source contract
# ---------------------------------------------------------------------------


def test_m11_revision_ids_and_marker_are_stable():
    """m11 revision 값이 지정된 형태에서 벗어나면 Alembic 체인이 끊기거나 PG
    VARCHAR(32) 제한을 넘는다. MARKER가 바뀌면 downgrade가 이전 시드를 찾지 못해
    orphan connection을 남긴다.
    """
    assert _m11.revision == "m11_custom_connection"
    assert len(_m11.revision) <= 32
    assert _m11.down_revision == "m10_prebuilt_connection"
    assert _m11.M11_SEED_MARKER == "[m11-auto-seed]"


def test_m11_migrate_custom_credentials_source_contract():
    """`_migrate_custom_credentials` 본체가 아래 불변성을 유지하는지 source-level
    가드. 실제 PG 왕복은 S6 integration 게이트에서 검증.

    불변성:
    1) 대상 SQL은 `type='custom' AND credential_id IS NOT NULL AND
       connection_id IS NULL`로 제한 (기 이관분 재처리 방지 → idempotent).
    2) `(user_id, credential_id)` 단위 dedup (N:1 공유).
    3) 재사용 lookup은 `(user_id, type='custom', credential_id)`만 매칭하고
       `display_name LIKE` 마커 필터를 **걸지 않는다** (Codex P1) — 사용자가
       M1 API로 미리 만든 manual CUSTOM connection도 SOT로 재사용해야 한다.
       마커는 INSERT 시점에만 부착해 downgrade에서 정확한 역삭제용.
    4) `uq_connections_one_default_per_scope` partial unique index 회피 —
       user별 CUSTOM scope에 default가 이미 있으면 새 connection은 `is_default=false`로 INSERT.
    5) tool.connection_id FK는 기존 NULL인 행에만 UPDATE (경합 조건에서 덮어쓰기 방지).
    """
    import inspect

    src = inspect.getsource(_m11._migrate_custom_credentials)
    # 1) 대상 식별 SQL — 3조건 모두 포함
    assert "type = 'custom'" in src
    assert "credential_id IS NOT NULL" in src
    assert "connection_id IS NULL" in src
    # 2) dedup 그룹화: (user_id, credential_id) tuple을 key로 사용
    assert "groups" in src
    assert "(user_id, credential_id)" in src
    # 3) 재사용 lookup은 마커-비의존(manual-respect) — 마커 LIKE는 lookup에 없어야 함
    assert "display_name LIKE :marker" not in src, (
        "upgrade lookup must not filter by marker — manual CUSTOM connections "
        "(created via M1 API before m11) must be reused as SOT (Codex 2차 P1)."
    )
    # 마커 상수는 INSERT display_name prefix로만 사용
    assert "M11_SEED_MARKER" in src
    # 3b) 재사용 lookup은 status='active' 필터 필수 — disabled 행을 재사용하면
    # legacy tool이 post-m11에 fail-closed로 일괄 실행 불가 회귀 (Codex 3차 P2).
    assert "status = 'active'" in src, (
        "upgrade lookup must filter by status='active' — binding to a disabled "
        "manual connection would break previously working tools after m11."
    )
    # 3c) Provenance INSERT — downgrade가 재사용된 manual connection에 바인딩된
    # tool까지 대칭 복원할 수 있도록 tool_id를 기록 (Codex adversarial 2차 [high] #1).
    assert "_m11_tool_backfill_provenance" in src, (
        "upgrade must record updated tool_ids into provenance table so that "
        "downgrade can symmetrically revert reused-manual-connection cases."
    )
    assert "ON CONFLICT (tool_id) DO NOTHING" in src, (
        "provenance INSERT must be idempotent for migration re-runs."
    )
    # 4) CUSTOM scope default 점유 pre-scan + is_default 플립
    assert "is_default = TRUE" in src
    assert "custom_default_taken" in src
    # 5) UPDATE tool.connection_id는 connection_id IS NULL 가드
    assert "connection_id IS NULL" in src
    assert "UPDATE tools SET connection_id" in src
    # CUSTOM 타입/provider 고정 — PREBUILT/MCP 침범 방지
    assert "'custom_api_key'" in src


def test_m11_preserves_tool_credential_id_during_own_upgrade():
    """m11 자체 upgrade 에서는 `tools.credential_id`를 NULL/DROP 처리하지
    않아야 한다 (m12 에서 drop 되기 전까지 legacy fallback 유지 가정 하에
    작성됨). m12 가 별도 revision 으로 분리돼 있으므로 m11 helper 본문 계약은
    여전히 유효하다.
    """
    import inspect

    src = inspect.getsource(_m11.upgrade) + inspect.getsource(_m11._migrate_custom_credentials)
    lowered = src.lower()
    assert not ("drop column" in lowered and "credential_id" in lowered), (
        "m11 upgrade must not drop tools.credential_id — the column drop is "
        "handled by a separate revision (m12)."
    )
    assert "set credential_id = null" not in lowered
    # tool.connection_id UPDATE만 수행
    assert "update tools set connection_id" in lowered


# ---------------------------------------------------------------------------
# Scenario 8 — m11 downgrade source contract
# ---------------------------------------------------------------------------


def test_m11_downgrade_only_deletes_seed_marker_rows():
    """downgrade는 `[m11-auto-seed]%` 마커가 붙은 connection만 DELETE하고,
    참조하던 tool.connection_id는 NULL로 역설정해야 한다. 수동 생성 CUSTOM
    connection(마커 없음)은 보존. tool 복원은 provenance 테이블 기반이라
    reused manual connection에 바인딩됐던 tool도 symmetric하게 NULL 복원된다
    (Codex adversarial 2차 [high] #1).

    추가 Codex adversarial 4차 [high] #2: dedup이 삭제한 duplicate connection
    은 `_m11_dedup_connection_snapshot`에서, repoint된 tool은
    `_m11_dedup_tool_remap`에서 완전 복원된다.
    """
    import inspect

    down_src = inspect.getsource(_m11.downgrade)
    # tool.connection_id NULL 역설정은 provenance 기반 — marker filter 아님
    assert "UPDATE tools SET connection_id = NULL" in down_src
    assert "_m11_tool_backfill_provenance" in down_src, (
        "downgrade must revert from provenance table (not marker LIKE on "
        "connections) to cover reused-manual-connection cases."
    )
    # dedup snapshot/remap 복원 (Codex 4차 [high] #2)
    assert "INSERT INTO connections" in down_src, (
        "downgrade must restore dedup-deleted connections from snapshot."
    )
    assert "_m11_dedup_connection_snapshot" in down_src
    assert "_m11_dedup_tool_remap" in down_src
    assert "UPDATE tools SET connection_id = r.original_connection_id" in down_src, (
        "downgrade must restore tools to their pre-dedup connection via remap table."
    )
    # connection DELETE는 여전히 marker LIKE 제한 (수동 생성분 보호)
    assert "display_name LIKE :m" in down_src
    assert "DELETE FROM connections" in down_src
    # 세 provenance 테이블 drop (upgrade step 1 역)
    assert "DROP TABLE IF EXISTS _m11_tool_backfill_provenance" in down_src
    assert "DROP TABLE IF EXISTS _m11_dedup_connection_snapshot" in down_src
    assert "DROP TABLE IF EXISTS _m11_dedup_tool_remap" in down_src
    # unique index drop (upgrade step 4 역)
    assert "DROP INDEX IF EXISTS uq_connections_custom_one_per_credential" in down_src
    # 순서 contract: dedup restore가 backfill NULL보다 앞서야 두 집합이 disjoint
    restore_idx = down_src.find("INSERT INTO connections")
    backfill_null_idx = down_src.find("UPDATE tools SET connection_id = NULL")
    assert restore_idx < backfill_null_idx, (
        "dedup restore must run before backfill NULL revert to keep the two "
        "tool-id sets disjoint and prevent double-reversion."
    )


def test_m11_upgrade_dedup_preexisting_custom_duplicates():
    """CREATE UNIQUE INDEX 전에 기존 `connections` 중복을 consolidate해야
    한다. m11 이전에는 제약이 없어 M1 API로 duplicate를 만들 수 있었다
    (Codex adversarial 2차 [high] #2). dedup 없이 CREATE UNIQUE INDEX만
    돌면 duplicate가 남아있는 DB에서 배포가 실패한다.

    Codex adversarial 4차 [high]:
    - canonical 선택에서 active를 is_default보다 먼저 고려해 disabled default
      행이 active 행을 canonicalize해 버리는 post-migration 장애 방지.
    - 삭제되는 duplicate 행과 repoint되는 tool을 snapshot/remap 테이블에
      기록해 downgrade가 완전 복원 가능하도록.
    """
    import inspect

    up_src = inspect.getsource(_m11.upgrade)
    assert "_dedup_preexisting_custom_duplicates" in up_src, (
        "upgrade must invoke duplicate cleanup before enforcing unique index."
    )
    # provenance 테이블 3종이 upgrade 시작에서 생성되어야 함
    assert "_m11_tool_backfill_provenance" in up_src
    assert "_m11_dedup_connection_snapshot" in up_src
    assert "_m11_dedup_tool_remap" in up_src

    dedup_src = inspect.getsource(_m11._dedup_preexisting_custom_duplicates)
    # (user_id, credential_id) 기반 GROUP BY ... HAVING COUNT(*) > 1
    assert "GROUP BY user_id, credential_id" in dedup_src
    assert "HAVING COUNT(*) > 1" in dedup_src
    # canonical ORDER: active 우선 → is_default → created_at → id (Codex 4차 [high] #1)
    # ORDER BY 본문에서 `(CASE WHEN status = 'active' ...)`가 `is_default DESC` 보다
    # 앞에 등장해야 health가 우선 고려된다.
    active_idx = dedup_src.find("CASE WHEN status = 'active'")
    default_idx = dedup_src.find("is_default DESC")
    assert active_idx != -1 and default_idx != -1
    assert active_idx < default_idx, (
        "canonical selection must prefer active status over is_default to avoid "
        "canonicalizing a disabled default and breaking working tools."
    )
    # snapshot + tool_remap INSERT (reversibility — Codex 4차 [high] #2)
    assert "INSERT INTO _m11_dedup_connection_snapshot" in dedup_src
    assert "INSERT INTO _m11_dedup_tool_remap" in dedup_src
    # duplicates에 바인딩된 tool 재매핑 후 DELETE (snapshot 뒤에 와야 함)
    assert "UPDATE tools SET connection_id = :canonical" in dedup_src
    assert "DELETE FROM connections WHERE id = :id" in dedup_src
    # snapshot INSERT가 DELETE보다 앞서 등장
    snapshot_idx = dedup_src.find("INSERT INTO _m11_dedup_connection_snapshot")
    delete_idx = dedup_src.find("DELETE FROM connections WHERE id = :id")
    assert snapshot_idx < delete_idx, (
        "snapshot must be written BEFORE deleting the duplicate row, otherwise "
        "downgrade cannot restore deleted connection metadata."
    )
    # tool_remap INSERT가 UPDATE보다 앞서 등장
    remap_idx = dedup_src.find("INSERT INTO _m11_dedup_tool_remap")
    update_idx = dedup_src.find("UPDATE tools SET connection_id = :canonical")
    assert remap_idx < update_idx, (
        "tool_remap must be recorded BEFORE repointing, otherwise downgrade "
        "cannot restore original tool→connection mappings."
    )
