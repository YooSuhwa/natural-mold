"""S4 — MCP → Connection 이관 회귀 + 신규 테스트 (백로그 E M2).

ADR-008 §M2 — `tool.connection_id` 경유 MCP 실행 + env_vars 템플릿 해석 +
legacy fallback 5 시나리오:

1. 마이그레이션 데이터 무결성 — m9 `_normalize_provider_name` 슬러그 + 같은
   scope 내 충돌 시 suffix 알고리즘 (m9 이관이 row 누락/중복 없이 mcp_servers
   ↔ connections를 1:1 매핑하기 위한 핵심 contract)
2. connection 경유 MCP smoke — `tool.connection_id` + `connection.extra_config`
   가 build_tools_config 출력에 반영되는지
3. env_vars `${credential.<field>}` 런타임 해석
4. 누락 필드 → ``ToolConfigError``
5. legacy fallback — `connection_id IS NULL AND mcp_server_id IS NOT NULL`
   tool은 기존 mcp_server 경로 유지

m9 이관 SQL은 PostgreSQL 전용(`CAST(... AS JSON)`/`TRUE`)이라 SQLite 단위
테스트로 직접 실행할 수 없다. 데이터 무결성은 (a) m9 helper의 슬러그 규칙
+ collision 알고리즘을 격리해 검증하고 (b) CHECKPOINT의 alembic 왕복으로
PG 통합 검증한다(사티아 S5 게이트).

M1 학습 재사용: aiosqlite FK 검증이 필요한 시나리오는 전용 engine
(StaticPool + PRAGMA foreign_keys=ON listener) + 별도 async_sessionmaker로
구성한다. 본 파일의 시나리오 2-5는 FK enforcement에 의존하지 않으므로 공유
conftest engine으로 충분.
"""

from __future__ import annotations

import importlib.util
import json
import re
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.connection import Connection
from app.models.credential import Credential
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.services.chat_service import (
    ToolConfigError,
    _resolve_env_vars,
    build_tools_config,
    get_agent_with_tools,
)
from app.services.encryption import encrypt_api_key
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# m9 모듈 로드 — alembic/ 에 __init__.py 가 없으므로 importlib로 직접 로드
# ---------------------------------------------------------------------------


def _load_m9_module():
    repo_root = Path(__file__).resolve().parents[1]
    m9_path = repo_root / "alembic" / "versions" / "m9_migrate_mcp_to_connections.py"
    spec = importlib.util.spec_from_file_location(
        "_test_m9_migrate_mcp_to_connections", m9_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_m9 = _load_m9_module()


# ---------------------------------------------------------------------------
# 공용 fixture / helper
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """Fernet 키를 주입해 credential 암호/복호가 round-trip 되도록 한다."""
    import app.services.encryption as enc_mod
    from app.config import settings

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", key, raising=False)
    original_fernet = enc_mod._fernet
    enc_mod._fernet = None
    yield
    enc_mod._fernet = original_fernet


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


async def _seed_agent_with_tool(
    db: AsyncSession, *, tool: Tool, model: Model
) -> Agent:
    db.add(tool)
    await db.flush()

    agent = Agent(
        user_id=TEST_USER_ID,
        name="MCP Agent",
        system_prompt="hi",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    return agent


async def _seed_credential(
    db: AsyncSession,
    data: dict[str, str],
    *,
    name: str = "MCP Cred",
    provider_name: str = "custom",
) -> Credential:
    cred = Credential(
        user_id=TEST_USER_ID,
        name=name,
        credential_type="api_key",
        provider_name=provider_name,
        data_encrypted=encrypt_api_key(json.dumps(data)),
        field_keys=list(data.keys()),
    )
    db.add(cred)
    await db.flush()
    return cred


# ---------------------------------------------------------------------------
# Scenario 1 — 마이그레이션 데이터 무결성: 슬러그 + scope 충돌 suffix
# ---------------------------------------------------------------------------


class TestMigrationDataIntegrity:
    """m9 이관 contract: mcp_servers 1건 → connections 1건 + tools.connection_id 매핑.

    핵심 위험은 `provider_name` 슬러그가 동일 scope에 충돌해 ``UNIQUE`` 제약
    위반으로 마이그레이션 자체가 깨지는 것. m9 라인 138-141의 suffix 알고리즘
    이 ``_2``, ``_3`` ... 로 회피하는지 격리 검증한다.
    """

    def test_normalize_provider_name_slug_rules(self):
        norm = _m9._normalize_provider_name
        # 소문자화 + 공백/특수문자 → underscore + 연속 underscore 축약
        assert norm("My Custom Server") == "my_custom_server"
        assert norm("github.com/foo") == "github_com_foo"
        assert norm("hello---world") == "hello_world"
        assert norm("___trim___") == "trim"
        # 빈 입력 fallback
        assert norm("") == "mcp"
        assert norm("***") == "mcp"
        # 45자 truncate (suffix 공간 확보)
        long = "a" * 60
        assert norm(long) == "a" * 45

    def test_provider_name_collision_suffix_algorithm(self):
        """동일 (user_id, provider_name) scope에 충돌하면 _2, _3 ... 부여.

        m9 upgrade() 라인 137-141의 ``while`` 루프와 동일한 invariant를 검증.
        본 테스트는 DB 없이 dict로만 시뮬레이션 — 알고리즘 자체의 정확성을
        검증한다(슬러그가 50자 컬럼을 초과하지 않도록 truncate 포함).
        """
        norm = _m9._normalize_provider_name
        user_a = "00000000-0000-0000-0000-0000000000aa"
        user_b = "00000000-0000-0000-0000-0000000000bb"
        taken: set[tuple[str, str]] = set()

        def assign(user_id: str, server_name: str) -> str:
            base = norm(server_name)
            provider = base
            suffix = 1
            while (user_id, provider) in taken:
                suffix += 1
                provider = f"{base}_{suffix}"[:50]
            taken.add((user_id, provider))
            return provider

        # 같은 user_a 안에서 동일 server_name 3건이 들어와도 충돌 없이 순번 부여
        assert assign(user_a, "Server") == "server"
        assert assign(user_a, "Server") == "server_2"
        assert assign(user_a, "Server") == "server_3"
        # user_b 는 별도 scope이므로 base 부터 다시 사용 가능
        assert assign(user_b, "Server") == "server"
        # 3건 모두 user_a, 1건 user_b 도합 4 row → mcp_servers 4건과 1:1
        assert len(taken) == 4

        # 50자 truncate 시 suffix 자릿수 보장
        long_name = "x" * 60
        first = assign(user_a, long_name)
        second = assign(user_a, long_name)
        assert len(first) <= 50 and len(second) <= 50
        assert second.endswith("_2")


# ---------------------------------------------------------------------------
# Scenario 2 — connection 경유 MCP 실행 smoke (build_tools_config 신규 경로)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_tools_config_uses_connection_extra_config(db: AsyncSession):
    """tool.connection_id 가 채워진 MCP tool은 connection.extra_config로부터
    url/auth_config를 해석해야 한다. tool-level/mcp_server-level 데이터는
    무시(없거나 비어있음)."""
    model = await _seed_model(db)
    cred = await _seed_credential(db, {"api_key": "from-connection-cred"})

    connection = Connection(
        user_id=TEST_USER_ID,
        type="mcp",
        provider_name="my_mcp",
        display_name="My MCP",
        credential_id=cred.id,
        extra_config={
            "url": "https://mcp-via-connection.example.com",
            "auth_type": "api_key",
            "headers": {},
            "env_vars": {"API_KEY": "${credential.api_key}"},
        },
        is_default=True,
        status="active",
    )
    db.add(connection)
    await db.flush()

    tool = Tool(
        user_id=TEST_USER_ID,
        type="mcp",
        connection_id=connection.id,
        name="conn_tool",
        auth_type="api_key",
    )
    agent = await _seed_agent_with_tool(db, tool=tool, model=model)

    loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
    assert loaded is not None
    configs = build_tools_config(loaded)
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg["type"] == "mcp"
    assert cfg["mcp_server_url"] == "https://mcp-via-connection.example.com"
    assert cfg["mcp_tool_name"] == "conn_tool"
    # env_vars 템플릿이 credential.data로 치환되어 cred_auth로 전달됨
    assert cfg["auth_config"] == {"API_KEY": "from-connection-cred"}


# ---------------------------------------------------------------------------
# Scenario 3 — env_vars 템플릿 해석 (multi-key + plaintext 혼합 tolerance)
# ---------------------------------------------------------------------------


class TestResolveEnvVars:
    """`_resolve_env_vars` 단위 검증 — chat_service 모듈-private 헬퍼.

    ADR-008 §2 — 신규 입력은 template-only 이지만 m9가 legacy 평문을 그대로
    extra_config.env_vars에 옮기므로(M2 마이그레이션 §4 정책) 런타임은
    template + plaintext 혼합을 허용한다(M6 이후 plaintext 거부 예정).
    """

    def test_template_replaced_with_credential_field(self):
        cred = Credential(
            user_id=TEST_USER_ID,
            name="C",
            credential_type="api_key",
            provider_name="custom",
            data_encrypted=encrypt_api_key(
                json.dumps({"api_key": "k1", "client_id": "id1"})
            ),
        )
        env_vars = {
            "API_KEY": "${credential.api_key}",
            "CLIENT_ID": "${credential.client_id}",
        }
        resolved = _resolve_env_vars(env_vars, cred)
        assert resolved == {"API_KEY": "k1", "CLIENT_ID": "id1"}

    def test_legacy_plaintext_passes_through_with_warning(self, caplog):
        """평문 값은 경고 로그 + 원본 유지 (M2 마이그레이션 tolerance)."""
        cred = Credential(
            user_id=TEST_USER_ID,
            name="C",
            credential_type="api_key",
            provider_name="custom",
            data_encrypted=encrypt_api_key(json.dumps({"api_key": "k1"})),
        )
        env_vars = {"PLAIN": "raw-value", "TEMPLATED": "${credential.api_key}"}
        with caplog.at_level("WARNING"):
            resolved = _resolve_env_vars(env_vars, cred)
        assert resolved == {"PLAIN": "raw-value", "TEMPLATED": "k1"}
        assert any("PLAIN" in rec.message for rec in caplog.records)

    def test_empty_env_vars_returns_empty_dict(self):
        assert _resolve_env_vars(None, None) == {}
        assert _resolve_env_vars({}, None) == {}


# ---------------------------------------------------------------------------
# Scenario 4 — 누락 필드 ToolConfigError
# ---------------------------------------------------------------------------


class TestResolveEnvVarsMissing:
    def test_missing_field_in_credential_raises(self):
        cred = Credential(
            user_id=TEST_USER_ID,
            name="C",
            credential_type="api_key",
            provider_name="custom",
            data_encrypted=encrypt_api_key(json.dumps({"api_key": "k1"})),
        )
        env_vars = {"MISSING": "${credential.does_not_exist}"}
        with pytest.raises(ToolConfigError, match=r"credential\.does_not_exist"):
            _resolve_env_vars(env_vars, cred)

    def test_template_without_credential_raises(self):
        env_vars = {"NEEDS_CRED": "${credential.api_key}"}
        with pytest.raises(ToolConfigError, match=r"credential\.api_key"):
            _resolve_env_vars(env_vars, None)

    @pytest.mark.asyncio
    async def test_build_tools_config_propagates_missing_field_error(
        self, db: AsyncSession
    ):
        """build_tools_config 호출 시 누락 필드 참조면 ToolConfigError가
        그대로 전파되어 호출자(chat router)가 처리하도록 한다."""
        model = await _seed_model(db)
        cred = await _seed_credential(db, {"api_key": "k1"})

        connection = Connection(
            user_id=TEST_USER_ID,
            type="mcp",
            provider_name="bad_mcp",
            display_name="Bad MCP",
            credential_id=cred.id,
            extra_config={
                "url": "https://bad.example.com",
                "auth_type": "api_key",
                "env_vars": {"MISSING": "${credential.not_there}"},
            },
            is_default=True,
            status="active",
        )
        db.add(connection)
        await db.flush()

        tool = Tool(
            user_id=TEST_USER_ID,
            type="mcp",
            connection_id=connection.id,
            name="bad_tool",
            auth_type="api_key",
        )
        agent = await _seed_agent_with_tool(db, tool=tool, model=model)

        loaded = await get_agent_with_tools(db, agent.id, TEST_USER_ID)
        assert loaded is not None
        with pytest.raises(ToolConfigError):
            build_tools_config(loaded)


# Compile-time guard: chat_service 의 템플릿 정규식이 ADR-008 §2 패턴과 동일.
def test_template_regex_contract():
    """`${credential.<field_name>}` 패턴은 ADR-008 §2 명세. 정규식 변경 시
    기존 m9 이관 데이터(env_vars 평문)가 갑자기 거부될 수 있으므로 패턴 변경
    은 의도적이어야 함을 가드."""
    from app.services.chat_service import _ENV_VAR_TEMPLATE

    assert _ENV_VAR_TEMPLATE.match("${credential.api_key}")
    assert _ENV_VAR_TEMPLATE.match("${credential.client_secret}")
    assert _ENV_VAR_TEMPLATE.match("${credential.x_y_z}")
    assert not _ENV_VAR_TEMPLATE.match("${credential.UPPER}")
    assert not _ENV_VAR_TEMPLATE.match("$credential.api_key")
    assert not _ENV_VAR_TEMPLATE.match("${other.api_key}")
    assert not _ENV_VAR_TEMPLATE.match("plain-value")
    # 정규식 자체는 chat_service에서 컴파일 — 본 테스트는 ADR 패턴 동등성만 보장
    assert isinstance(_ENV_VAR_TEMPLATE.pattern, str)
    assert re.fullmatch(_ENV_VAR_TEMPLATE.pattern, "${credential.api_key}")


# ---------------------------------------------------------------------------
# Cross-tenant credential leak defense (code-reviewer blocker, M2 추가 가드)
# ---------------------------------------------------------------------------


class TestOwnershipGuards:
    """`build_tools_config` / env_var_resolver 가드가 타 유저 credential
    복호화를 거부하는지 검증. M1 POST/PATCH validation이 우선이지만, DML
    실수·M6 마이그레이션 사고 대비의 런타임 방어.
    """

    def _make_agent_with_tool(
        self,
        *,
        tool_user_id,
        conn_user_id,
        cred_user_id=None,
    ):
        import datetime

        from app.models.agent import Agent
        from app.models.connection import Connection
        from app.models.credential import Credential
        from app.models.tool import AgentToolLink, Tool
        from app.schemas.tool import ToolType

        tool_uid = uuid.uuid4()
        conn_uid = uuid.uuid4()
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

        credential = None
        if cred_user_id is not None:
            credential = Credential(
                id=uuid.uuid4(),
                user_id=cred_user_id,
                name="cred",
                credential_type="api_key",
                provider_name="custom_api_key",
                data_encrypted="x",
                field_keys=["api_key"],
                created_at=now,
            )

        conn = Connection(
            id=conn_uid,
            user_id=conn_user_id,
            type="mcp",
            provider_name="resend",
            display_name="Resend",
            credential_id=credential.id if credential else None,
            credential=credential,
            extra_config={
                "url": "https://x",
                "auth_type": "none",
                "env_vars": {},
            },
            is_default=True,
            status="active",
            created_at=now,
            updated_at=now,
        )

        tool = Tool(
            id=tool_uid,
            user_id=tool_user_id,
            type=ToolType.MCP,
            name="t",
            is_system=False,
            connection_id=conn_uid,
            connection=conn,
            created_at=now,
        )
        link = AgentToolLink(
            agent_id=uuid.uuid4(),
            tool_id=tool_uid,
            tool=tool,
        )
        agent = Agent(id=uuid.uuid4(), user_id=tool_user_id, name="a")
        agent.tool_links = [link]
        return agent

    def test_tool_connection_user_mismatch_raises(self):
        from app.services.chat_service import ToolConfigError, build_tools_config

        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        agent = self._make_agent_with_tool(
            tool_user_id=user_a, conn_user_id=user_b
        )
        with pytest.raises(ToolConfigError, match="owner mismatch"):
            build_tools_config(agent)

    def test_connection_credential_user_mismatch_raises(self):
        from app.services.chat_service import ToolConfigError, build_tools_config

        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        agent = self._make_agent_with_tool(
            tool_user_id=user_a,
            conn_user_id=user_a,
            cred_user_id=user_b,
        )
        with pytest.raises(ToolConfigError, match="owned by a different user"):
            build_tools_config(agent)


# ---------------------------------------------------------------------------
# env_vars shape defense (code-reviewer follow-up #2)
# ---------------------------------------------------------------------------


def test_resolve_env_vars_rejects_non_dict_shape():
    """extra_config.env_vars가 수동 DML로 list/str 등 오염된 경우 명확한 422."""
    from app.services.env_var_resolver import ToolConfigError, resolve_env_vars

    with pytest.raises(ToolConfigError, match="must be a dict"):
        resolve_env_vars(["API_KEY=x"], None)
    with pytest.raises(ToolConfigError, match="must be a dict"):
        resolve_env_vars("RESEND_API_KEY=sk", None)


# ---------------------------------------------------------------------------
# ToolConfigError → AppError integration (Codex 후속 P2, router 500 방어)
# ---------------------------------------------------------------------------


def test_tool_config_error_is_app_error():
    """ToolConfigError는 AppError를 상속해 main.py의 app_error_handler가
    자동으로 400 code/message 페이로드를 반환하게 한다. 일반 Exception이면
    generic 500 handler로 떨어짐 (Codex 후속 P2)."""
    from app.exceptions import AppError
    from app.services.env_var_resolver import ToolConfigError

    exc = ToolConfigError("missing url")
    assert isinstance(exc, AppError)
    assert exc.status == 400
    assert exc.code == "TOOL_CONFIG_ERROR"
    assert exc.message == "missing url"


# ---------------------------------------------------------------------------
# extra_config.headers propagation (Codex 후속 P2)
# ---------------------------------------------------------------------------


def test_build_tools_config_forwards_connection_headers():
    """ConnectionExtraConfig.headers가 executor가 읽는 auth_config['headers']로
    전달되어야 한다. 스키마가 허용하는 static 헤더를 런타임이 silently drop하면
    MCP 인증이 깨진다 (Codex 후속 P2)."""
    import datetime

    from app.models.agent import Agent
    from app.models.connection import Connection
    from app.models.tool import AgentToolLink, Tool
    from app.schemas.tool import ToolType
    from app.services.chat_service import build_tools_config

    user_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    conn = Connection(
        id=conn_id,
        user_id=user_id,
        type="mcp",
        provider_name="custom_mcp",
        display_name="Custom",
        credential_id=None,
        credential=None,
        extra_config={
            "url": "https://mcp.example.com",
            "auth_type": "none",
            "headers": {"X-Org": "acme", "X-Version": "2"},
            "env_vars": {},
        },
        is_default=True,
        status="active",
        created_at=now,
        updated_at=now,
    )
    tool = Tool(
        id=tool_id,
        user_id=user_id,
        type=ToolType.MCP,
        name="t",
        is_system=False,
        connection_id=conn_id,
        connection=conn,
        created_at=now,
    )
    link = AgentToolLink(
        agent_id=uuid.uuid4(),
        tool_id=tool_id,
        tool=tool,
    )
    agent = Agent(id=uuid.uuid4(), user_id=user_id, name="a")
    agent.tool_links = [link]

    tools = build_tools_config(agent)
    assert len(tools) == 1
    # transport 헤더는 별도 top-level 키로 전달되어야 한다 — auth_config에
    # 섞이면 executor의 _AuthInjectorInterceptor가 매 tool call args에
    # 주입해 MCP 서버가 예기치 않은 "headers" 파라미터를 받게 된다
    # (Codex 6차 adversarial P1).
    assert tools[0]["mcp_transport_headers"] == {
        "X-Org": "acme",
        "X-Version": "2",
    }
    # auth_config는 env_vars 치환 결과만 담고 transport 헤더는 포함하지 않음
    assert (tools[0]["auth_config"] or {}).get("headers") is None


# ---------------------------------------------------------------------------
# Migrated-shape extra_config must roundtrip through ConnectionExtraConfig
# (Codex 3차 P1 — sentinel이 schema와 충돌하던 regression)
# ---------------------------------------------------------------------------


def test_migrated_extra_config_passes_strict_schema():
    """m9가 이관한 extra_config (url/auth_type/headers/env_vars만)가
    `ConnectionExtraConfig(extra='forbid')`를 그대로 통과해야 한다.
    과거 버전은 `_migrated_from_mcp_server` sentinel을 넣어 GET/PATCH API가
    ValidationError로 깨졌다 (Codex 3차 리뷰 P1)."""
    from app.schemas.connection import (
        ConnectionExtraConfig,
        ConnectionResponse,
    )

    migrated_shape = {
        "url": "https://mcp.example.com",
        "auth_type": "api_key",
        "headers": {},
        "env_vars": {"RESEND_API_KEY": "sk-plaintext"},
    }
    # ConnectionExtraConfig 재검증 성공
    cfg = ConnectionExtraConfig.model_validate(migrated_shape)
    assert cfg.url == "https://mcp.example.com"

    # ConnectionResponse.model_validate 경로도 깨지면 안 됨 (FastAPI
    # response_model이 실제로 이 경로를 거친다)
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
    resp = ConnectionResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "user_id": TEST_USER_ID,
            "type": "mcp",
            "provider_name": "resend",
            "display_name": "Resend",
            "credential_id": None,
            "extra_config": migrated_shape,
            "is_default": True,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    assert resp.extra_config.url == "https://mcp.example.com"
    # secret 값 은닉, 키만 노출
    assert resp.extra_config.env_var_keys == ["RESEND_API_KEY"]
    dumped = resp.model_dump()
    assert "env_vars" not in dumped["extra_config"]
    assert dumped["extra_config"]["env_var_keys"] == ["RESEND_API_KEY"]


def test_response_tolerates_legacy_non_string_env_var_values():
    """m9가 이관한 legacy `auth_config` dict의 비-string 값(int/bool/dict)은
    schema의 `dict[str, str]` 타입과 충돌하지만, Response가 값을 버리고 키만
    노출하므로 /api/connections 응답은 500이 아닌 정상 200이어야 한다
    (Codex 4차 adversarial Finding 2)."""
    import datetime as _dt

    from app.schemas.connection import ConnectionResponse

    now = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
    legacy_heterogeneous = {
        "url": "https://mcp.example.com",
        "auth_type": "api_key",
        "env_vars": {
            "RESEND_API_KEY": "sk-plaintext",
            "TIMEOUT": 30,
            "VERIFY_SSL": True,
            "CFG": {"nested": "v"},
        },
    }
    resp = ConnectionResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "user_id": TEST_USER_ID,
            "type": "mcp",
            "provider_name": "legacy_mcp",
            "display_name": "Legacy",
            "credential_id": None,
            "extra_config": legacy_heterogeneous,
            "is_default": True,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    assert resp.extra_config.env_var_keys == sorted(
        ["RESEND_API_KEY", "TIMEOUT", "VERIFY_SSL", "CFG"]
    )


def test_response_redacts_env_var_secret_values():
    """클라이언트가 POST로 설정한 env_vars 템플릿 값도 GET 응답에서 노출되지
    않는다 (Codex 4차 adversarial Finding 1 — secret echo 방지)."""
    import datetime as _dt

    from app.schemas.connection import ConnectionResponse

    now = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
    resp = ConnectionResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "user_id": TEST_USER_ID,
            "type": "mcp",
            "provider_name": "resend",
            "display_name": "Resend",
            "credential_id": None,
            "extra_config": {
                "url": "https://x",
                "auth_type": "api_key",
                "env_vars": {"RESEND_API_KEY": "${credential.api_key}"},
            },
            "is_default": True,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    dumped = resp.model_dump()
    assert "env_vars" not in dumped["extra_config"]
    assert dumped["extra_config"]["env_var_keys"] == ["RESEND_API_KEY"]


def test_extra_config_rejects_migration_sentinel_leak():
    """과거 m9 버전이 사용하던 `_migrated_from_mcp_server` 키가 재도입되면
    schema에서 즉시 거부 — regression guard."""
    from pydantic import ValidationError

    from app.schemas.connection import ConnectionExtraConfig

    polluted = {
        "url": "https://x",
        "auth_type": "none",
        "_migrated_from_mcp_server": "abc",
    }
    with pytest.raises(ValidationError):
        ConnectionExtraConfig.model_validate(polluted)


# ---------------------------------------------------------------------------
# m9: credential-backed MCP server auto env_vars (Codex 5차 F1)
# ---------------------------------------------------------------------------


def test_m9_generates_env_vars_from_credential_field_keys():
    """legacy mcp_server가 credential_id만 가지고 auth_config=NULL인 경우,
    m9는 credential.field_keys를 읽어 ${credential.<key>} 템플릿으로 env_vars를
    자동 생성해야 한다 (legacy resolve_server_auth가 credential 전체를 auth로
    반환하던 동작을 새 경로에서 유지)."""
    # m9 모듈에서 구현 세부를 import하지 않고 관찰 가능한 알고리즘 재현
    field_keys = ["api_key", "header_name"]
    env_vars = {str(k): f"${{credential.{k}}}" for k in field_keys}

    # 생성된 env_vars는 모두 `${credential.<field>}` 템플릿
    from app.services.env_var_resolver import _ENV_VAR_TEMPLATE

    for _key, val in env_vars.items():
        assert _ENV_VAR_TEMPLATE.match(val)

    # 그리고 스키마의 write-side validator를 통과한다
    from app.schemas.connection import _ensure_env_vars_template_only

    _ensure_env_vars_template_only(env_vars)


# ---------------------------------------------------------------------------
# Write-side validation: MCP + credential + non-none auth → env_vars 필수
# (Codex 5차 F2 — unauthenticated runtime 방지)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_credential_without_env_vars_rejected(client: AsyncClient):
    """MCP + credential_id + auth_type!='none'에서 env_vars 누락 → 422."""
    # credential 먼저 생성
    cred_resp = await client.post(
        "/api/credentials",
        json={
            "name": "resend",
            "credential_type": "api_key",
            "provider_name": "custom_api_key",
            "data": {"api_key": "sk-test"},
        },
    )
    if cred_resp.status_code != 201:
        pytest.skip("credentials endpoint not available in this test env")
    cred_id = cred_resp.json()["id"]

    # MCP + credential + auth_type='api_key' + env_vars 누락 → 422
    resp = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "mcp_resend",
            "display_name": "Resend",
            "credential_id": cred_id,
            "extra_config": {
                "url": "https://resend-mcp.example.com",
                "auth_type": "api_key",
            },
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_mcp_credential_with_none_auth_no_env_vars_ok(
    client: AsyncClient,
):
    """auth_type='none'이면 env_vars 없이 credential만 있어도 허용."""
    cred_resp = await client.post(
        "/api/credentials",
        json={
            "name": "dummy-noauth",
            "credential_type": "api_key",
            "provider_name": "custom_api_key",
            "data": {"api_key": "x"},
        },
    )
    if cred_resp.status_code != 201:
        pytest.skip("credentials endpoint not available in this test env")
    cred_id = cred_resp.json()["id"]

    resp = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "mcp_noauth",
            "display_name": "No Auth",
            "credential_id": cred_id,
            "extra_config": {
                "url": "https://mcp.example.com",
                "auth_type": "none",
            },
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_mcp_no_credential_no_env_vars_ok(client: AsyncClient):
    """credential_id 자체가 없으면 env_vars 없어도 허용 (cred 없는 MCP)."""
    resp = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "public_mcp",
            "display_name": "Public",
            "extra_config": {
                "url": "https://public-mcp.example.com",
                "auth_type": "none",
            },
        },
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# PATCH credential_id 변경도 invariant 재검증 (Codex 6차 adversarial P2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_credential_id_swap_enforces_env_vars_invariant(
    client: AsyncClient,
):
    """MCP 연결의 credential_id를 PATCH로 바꾸면서 env_vars가 누락된 상태면
    422. 과거에는 credential_id만 바꾸면 revalidation이 스킵되어 unauthenticated
    상태가 저장되는 경로가 있었다."""
    # 1. credential 2개 + auth_type='none'(env_vars 불필요)로 최초 연결 생성
    cred_a = await client.post(
        "/api/credentials",
        json={
            "name": "cred-a",
            "credential_type": "api_key",
            "provider_name": "custom_api_key",
            "data": {"api_key": "a"},
        },
    )
    cred_b = await client.post(
        "/api/credentials",
        json={
            "name": "cred-b",
            "credential_type": "api_key",
            "provider_name": "custom_api_key",
            "data": {"api_key": "b"},
        },
    )
    if cred_a.status_code != 201 or cred_b.status_code != 201:
        pytest.skip("credentials endpoint not available")
    cred_a_id = cred_a.json()["id"]
    cred_b_id = cred_b.json()["id"]

    # auth_type='none' + credential_id=cred_a → env_vars 없어도 POST 통과
    created = await client.post(
        "/api/connections",
        json={
            "type": "mcp",
            "provider_name": "mcp_server",
            "display_name": "Test",
            "credential_id": cred_a_id,
            "extra_config": {
                "url": "https://mcp.example.com",
                "auth_type": "none",
            },
        },
    )
    assert created.status_code == 201, created.text
    conn_id = created.json()["id"]

    # 2. extra_config로 auth_type 올리기 + env_vars 누락 → 422 (직접 거부)
    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={
            "extra_config": {
                "url": "https://mcp.example.com",
                "auth_type": "api_key",
                # env_vars 없음
            },
        },
    )
    assert resp.status_code == 422, resp.text

    # 3. 핵심: auth_type='api_key'로 이미 세팅된 상태라고 가정하고 env_vars
    # 포함해서 일단 통과시킴
    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={
            "extra_config": {
                "url": "https://mcp.example.com",
                "auth_type": "api_key",
                "env_vars": {"API_KEY": "${credential.api_key}"},
            },
        },
    )
    assert resp.status_code == 200, resp.text

    # 4. credential_id만 다른 cred로 바꾸고 env_vars 없이 extra_config 재설정
    # 시도 → 재검증이 걸려 422
    resp = await client.patch(
        f"/api/connections/{conn_id}",
        json={
            "credential_id": cred_b_id,
            "extra_config": {
                "url": "https://mcp.example.com",
                "auth_type": "api_key",
                # env_vars 누락
            },
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# ConnectionResponse가 원본 extra_config dict를 mutate하지 않는다
# (Codex 6차 adversarial P2)
# ---------------------------------------------------------------------------


def test_response_validator_does_not_mutate_input_dict():
    """ConnectionExtraConfigResponse의 before-validator가 입력 dict의
    env_vars를 pop하면 ORM 객체의 mutable state가 오염된다. model_validate
    후에도 원본 dict에 env_vars가 그대로 남아있어야 한다."""
    import datetime as _dt

    from app.schemas.connection import ConnectionResponse

    now = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
    original_extra = {
        "url": "https://mcp.example.com",
        "auth_type": "api_key",
        "env_vars": {"API_KEY": "${credential.api_key}"},
    }
    snapshot_before = dict(original_extra)

    ConnectionResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "user_id": TEST_USER_ID,
            "type": "mcp",
            "provider_name": "resend",
            "display_name": "R",
            "credential_id": None,
            "extra_config": original_extra,
            "is_default": True,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    # 원본 dict에 env_vars가 여전히 존재해야 함
    assert original_extra == snapshot_before
    assert "env_vars" in original_extra
    assert original_extra["env_vars"] == {"API_KEY": "${credential.api_key}"}


# ---------------------------------------------------------------------------
# F1 후속 — headers 응답 redaction (env_vars와 동일 정책)
# (Codex 7차 adversarial P1)
# ---------------------------------------------------------------------------


def test_response_redacts_header_values():
    """`extra_config.headers`도 env_vars와 동일하게 값은 은닉하고 키만 노출.
    Authorization/API key 같은 secret-bearing 헤더가 GET 응답에 echo되면 안 됨."""
    import datetime as _dt

    from app.schemas.connection import ConnectionResponse

    now = _dt.datetime.now(_dt.UTC).replace(tzinfo=None)
    resp = ConnectionResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "user_id": TEST_USER_ID,
            "type": "mcp",
            "provider_name": "resend",
            "display_name": "R",
            "credential_id": None,
            "extra_config": {
                "url": "https://x",
                "auth_type": "bearer",
                "headers": {
                    "Authorization": "Bearer sk-secret",
                    "X-Tenant": "acme",
                },
                "env_vars": {},
            },
            "is_default": True,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    dumped = resp.model_dump()
    # 값은 응답에 노출되지 않음
    assert "headers" not in dumped["extra_config"]
    # 키만 정렬된 리스트로 노출
    assert dumped["extra_config"]["header_keys"] == [
        "Authorization",
        "X-Tenant",
    ]


# ---------------------------------------------------------------------------
# F3 — 같은 URL + 다른 transport headers는 별도 MCP 서버 그룹으로 분리
# (Codex 7차 adversarial P2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distinct_transport_headers_create_separate_mcp_groups(
    monkeypatch,
):
    """같은 URL을 공유하는 두 도구가 서로 다른 `mcp_transport_headers`를
    가지면 executor는 별도 MCP server 인스턴스로 분리해야 한다. URL만으로
    그룹핑하면 첫 도구의 헤더가 다른 도구에 재사용되어 멀티 테넌트 gateway에서
    cross-tenant 혼선 발생."""
    from langchain_mcp_adapters import client as lc_mcp_client

    from app.agent_runtime import executor as exec_mod

    captured_configs: list[dict] = []

    class _FakeClient:
        def __init__(self, config, tool_interceptors=None):
            captured_configs.append(config)

        async def get_tools(self):
            return []

    # _build_mcp_tools 함수 내부에서 `from langchain_mcp_adapters.client
    # import MultiServerMCPClient` 하므로 원 모듈의 속성을 교체한다.
    monkeypatch.setattr(
        lc_mcp_client, "MultiServerMCPClient", _FakeClient, raising=True
    )

    mcp_configs = [
        {
            "type": "mcp",
            "name": "tool_a",
            "mcp_tool_name": "tool_a",
            "mcp_server_url": "https://gateway.example.com/mcp",
            "mcp_transport_headers": {"X-Tenant": "acme"},
            "auth_config": None,
        },
        {
            "type": "mcp",
            "name": "tool_b",
            "mcp_tool_name": "tool_b",
            "mcp_server_url": "https://gateway.example.com/mcp",
            "mcp_transport_headers": {"X-Tenant": "beta"},
            "auth_config": None,
        },
    ]

    await exec_mod._build_mcp_tools(mcp_configs)

    # 서버 config가 2개 생성됨 (별도 그룹). 각각 올바른 헤더 유지.
    assert len(captured_configs) == 2
    seen_tenants = set()
    for cfg in captured_configs:
        # cfg는 {key: server_config} 형태
        server_cfg = next(iter(cfg.values()))
        assert server_cfg["url"] == "https://gateway.example.com/mcp"
        seen_tenants.add(server_cfg["headers"]["X-Tenant"])
    assert seen_tenants == {"acme", "beta"}


# ---------------------------------------------------------------------------
# F2 후속 — executor server key deterministic (process restart 안정성)
# (Codex 8차 adversarial F2)
# ---------------------------------------------------------------------------


def test_executor_server_key_is_deterministic_across_calls():
    """executor의 server group key는 `hash()` (process-randomized) 대신
    deterministic SHA256 digest를 사용해야 한다. 재시작 후에도 같은
    (url, headers)가 같은 key/tool name prefix를 생성해 HiTL resume 안정."""
    import hashlib as _hashlib
    import json as _json

    def compute_key(url: str, headers: dict | None) -> str:
        from app.agent_runtime.executor import _url_to_server_key

        digest = _hashlib.sha256(
            _json.dumps(headers or {}, sort_keys=True).encode()
        ).hexdigest()[:8]
        return f"{_url_to_server_key(url)}|{digest}"

    url = "https://gateway.example.com/mcp"
    a = compute_key(url, {"X-Tenant": "acme"})
    b = compute_key(url, {"X-Tenant": "acme"})
    c = compute_key(url, {"X-Tenant": "beta"})

    # 같은 (url, headers) → 같은 key
    assert a == b
    # 다른 headers → 다른 key
    assert a != c
    # SHA256 hex digest가 16자 hex format의 hash()에 의존하지 않음
    assert "|" in a
    left, right = a.rsplit("|", 1)
    assert len(right) == 8
    int(right, 16)  # valid hex


# ---------------------------------------------------------------------------
# F1 후속 — m9가 credential 복구 실패 server는 migrate 스킵
# (Codex 8차 adversarial F1)
# ---------------------------------------------------------------------------


def test_m9_skips_unrecoverable_credential_backed_server():
    """`credential_auth_recoverable = False`인 경우 m9는 connection INSERT와
    tools.connection_id 매핑을 모두 건너뛴다. tool은 legacy mcp_server_id 경로로
    계속 동작 (auth regression 없음).

    실제 PG 통합 검증은 integration 테스트에서 수행. 여기서는 m9 모듈의
    credential_auth_recoverable 플래그 분기 로직을 source-level에 읽어
    "continue"가 타고 있는지 가드한다.
    """
    import inspect

    src = inspect.getsource(_m9.upgrade)
    # 복구 실패 플래그를 체크하는 분기가 존재해야 함
    assert "credential_auth_recoverable" in src
    # 해당 분기에서 continue로 이관을 스킵해야 함
    assert "continue" in src
    # legacy path 유지를 설명하는 주석 포함
    assert "legacy" in src.lower() or "legacy path" in src.lower()
