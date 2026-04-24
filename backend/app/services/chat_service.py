from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.connection import Connection
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.skill import AgentSkillLink
from app.models.token_usage import TokenUsage
from app.models.tool import AgentToolLink, MCPServer, Tool
from app.schemas.conversation import ConversationUpdate
from app.schemas.tool import ToolType
from app.services.connection_service import (
    get_default_connections_for_providers,
)
from app.services.credential_service import (
    resolve_credential_data,
    resolve_server_auth,
)
from app.services.env_var_resolver import (
    _ENV_VAR_TEMPLATE,
    ToolConfigError,
    assert_connection_ownership,
    assert_credential_ownership,
    resolve_env_vars,
)

logger = logging.getLogger(__name__)

# Backwards-compat re-exports for existing tests that import from chat_service
_resolve_env_vars = resolve_env_vars
__all__ = [
    "_ENV_VAR_TEMPLATE",
    "ToolConfigError",
    "_resolve_env_vars",
    "_load_user_default_connection_map",
    "_resolve_prebuilt_auth",
    "_resolve_custom_auth",
    "build_tools_config",
]


async def _load_user_default_connection_map(
    db: AsyncSession,
    agent: Agent,
    user_id: uuid.UUID,
) -> dict[str, Connection]:
    """Build per-user default connection map for PREBUILT tools on an agent.

    ADR-008 §3. Tool은 공유 행(`user_id=NULL`)이라 connection의 SOT는
    `(current_user_id, tool.provider_name)` 조합. N+1 방지를 위해 agent의 모든
    PREBUILT tool에서 distinct `provider_name`을 모아 IN 쿼리 1회로 로드.

    **cross-tenant 가드 hook**: 내부 bulk 쿼리에 `user_id` 필터가 걸려 있으므로
    user_A agent로 호출해도 user_B의 connection이 섞이지 않는다. S5 회귀
    테스트가 이 함수를 직접 호출해 격리를 검증할 수 있도록 모듈-private로
    노출.
    """
    provider_names: set[str] = {
        link.tool.provider_name
        for link in agent.tool_links
        if link.tool.type == ToolType.PREBUILT and link.tool.provider_name
    }
    return await get_default_connections_for_providers(
        db, user_id, "prebuilt", provider_names
    )


async def list_conversations(db: AsyncSession, agent_id: uuid.UUID) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.agent_id == agent_id)
        .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def create_conversation(
    db: AsyncSession, agent_id: uuid.UUID, title: str | None = None
) -> Conversation:
    conv = Conversation(agent_id=agent_id, title=title or "새 대화")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    return result.scalar_one_or_none()


async def update_conversation(
    db: AsyncSession, conv: Conversation, data: ConversationUpdate
) -> Conversation:
    if data.title is not None:
        conv.title = data.title
    if data.is_pinned is not None:
        conv.is_pinned = data.is_pinned
    await db.commit()
    await db.refresh(conv)
    return conv


async def delete_conversation(db: AsyncSession, conv: Conversation) -> None:
    from app.agent_runtime.checkpointer import delete_thread

    await delete_thread(str(conv.id))
    await db.delete(conv)
    await db.commit()


async def list_messages_from_checkpointer(
    conversation_id: uuid.UUID,
    base_timestamp: datetime | None = None,
) -> list:
    """Checkpointer에서 대화 메시지를 조회하여 MessageResponse 리스트로 반환."""
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.message_utils import langchain_messages_to_response

    checkpointer = get_checkpointer()
    config = {"configurable": {"thread_id": str(conversation_id)}}
    checkpoint_tuple = await checkpointer.aget_tuple(config)

    if not checkpoint_tuple:
        return []

    messages = checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", [])
    return langchain_messages_to_response(messages, conversation_id, base_timestamp)


async def maybe_set_auto_title(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
) -> None:
    """첫 사용자 메시지일 때 대화 제목을 자동 설정.

    Conversation.title이 기본값('새 대화')인 경우에만 UPDATE 실행.
    """
    title = content.strip().replace("\n", " ")
    if len(title) > 40:
        title = title[:37] + "..."
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.title == "새 대화")
        .values(title=title)
    )
    await db.commit()


async def save_token_usage(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost: float | None = None,
) -> TokenUsage:
    usage = TokenUsage(
        conversation_id=conversation_id,
        agent_id=agent_id,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost=estimated_cost,
    )
    db.add(usage)
    await db.commit()
    return usage


async def get_agent_with_tools(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> Agent | None:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(
            selectinload(Agent.model).selectinload(Model.llm_provider),
            selectinload(Agent.tool_links)
            .selectinload(AgentToolLink.tool)
            .selectinload(Tool.mcp_server)
            .selectinload(MCPServer.credential),
            selectinload(Agent.tool_links)
            .selectinload(AgentToolLink.tool)
            .selectinload(Tool.connection)
            .selectinload(Connection.credential),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        return None

    # PREBUILT 도구의 per-user default connection 프리로드. tool별 selectinload로는
    # 체인할 수 없는 스코프 — Connection은 (user_id, type, provider_name)로 찾고,
    # tool.connection_id가 아니라 current_user와 tool.provider_name의 조합으로
    # 매칭되기 때문(ADR-008 §3). 테스트에서 user_id 필터를 직접 검증할 수 있도록
    # 모듈-private 헬퍼로 분리.
    default_conn_map = await _load_user_default_connection_map(db, agent, user_id)
    # build_tools_config(sync)에서 조회할 수 있도록 agent 객체에 attach.
    # 반환 시그니처를 건드리지 않는 비침투적 경로.
    agent._default_connection_map = default_conn_map  # type: ignore[attr-defined]
    return agent


def build_effective_prompt(agent: Agent) -> str:
    """Build system prompt (skill injection handled by deepagents SkillsMiddleware)."""
    return agent.system_prompt


def build_agent_skills(agent: Agent) -> list[dict[str, Any]]:
    """Build agent_skills list from agent's skill links (package skills with storage_path)."""
    return [
        {"skill_id": str(link.skill.id), "storage_path": link.skill.storage_path}
        for link in agent.skill_links
        if link.skill and link.skill.storage_path
    ]


def _gate_connection_active(tool: Tool, conn: Connection) -> None:
    """Gate A: ownership + `status='active'` (kill-switch).

    Shared by PREBUILT and CUSTOM resolvers. Must run before any credential
    read so a disabled connection cannot be bypassed by tool-level fallback.
    Raises `ToolConfigError` on failure.
    """
    assert_connection_ownership(
        tool_user_id=tool.user_id,
        connection_user_id=conn.user_id,
        connection_id=conn.id,
        tool_name=tool.name,
    )
    if conn.status != "active":
        raise ToolConfigError(
            f"Tool '{tool.name}' connection {conn.id} is "
            f"status='{conn.status}' — execution blocked. "
            "Reactivate the connection to run this tool."
        )


def _gate_connection_credential(tool: Tool, conn: Connection) -> dict[str, Any]:
    """Gate B: credential ownership + `credential IS NOT NULL` + decrypt.

    Shared by PREBUILT and CUSTOM resolvers. Assumes Gate A has passed.
    Returns the decrypted auth dict.
    """
    assert_credential_ownership(
        connection_user_id=conn.user_id,
        credential=conn.credential,
        connection_id=conn.id,
    )
    if conn.credential is None:
        raise ToolConfigError(
            f"Tool '{tool.name}' connection {conn.id} has no bound "
            "credential — execution blocked. Bind a credential or delete the "
            "connection."
        )
    return resolve_credential_data(conn.credential)


def _resolve_prebuilt_auth(
    tool: Tool,
    default_connection_map: dict[str, Connection],
) -> dict[str, Any]:
    """Resolve PREBUILT tool auth via per-user default Connection.

    ADR-008 §3 + §11. PREBUILT shared rows lookup by
    `(current_user, tool.provider_name)`. Caller: `tool.type == PREBUILT AND
    tool.provider_name IS NOT NULL`.

    - connection 자체가 없음 → `{}` (env fallback, ADR-008 §11)
    - connection 있음 → Gate A (ownership + active) → Gate B (credential decrypt)
    """
    conn = default_connection_map.get(tool.provider_name)
    if conn is None:
        # env fallback — tool builder가 `settings.*`를 사용. 여기서 settings를
        # 재조회하지 않는다 (2중 fallback 금지).
        return {}
    _gate_connection_active(tool, conn)
    return _gate_connection_credential(tool, conn)


def _resolve_custom_auth(tool: Tool) -> dict[str, Any]:
    """Resolve CUSTOM tool auth via bound Connection (ADR-008 §4 M4).

    M6 cleanup: legacy tool.credential_id / tool.auth_config 컬럼이 drop
    되었으므로 connection_id 없는 CUSTOM tool은 fail-closed.

    - `connection_id IS NULL` → `ToolConfigError` (fail-closed)
    - `connection_id` 있으나 relationship None → FK dangling → `ToolConfigError`
    - connection 있음 → Gate A (kill-switch) → Gate B (credential decrypt)
    """
    if tool.connection_id is None:
        raise ToolConfigError(
            f"CUSTOM tool '{tool.name}' has no connection_id — execution "
            "blocked. Bind a connection to run this tool."
        )
    if tool.connection is None:
        # async SQLAlchemy에서 lazy-load는 MissingGreenlet raise이므로 None은
        # FK dangling(connection row 삭제) 또는 eager-load 누락.
        raise ToolConfigError(
            f"CUSTOM tool '{tool.name}' has connection_id={tool.connection_id} "
            "but the connection relationship is missing — execution blocked. "
            "Caller must eager-load Tool.connection or the connection row was "
            "deleted out-of-band."
        )

    conn = tool.connection
    _gate_connection_active(tool, conn)
    return _gate_connection_credential(tool, conn)


def build_tools_config(agent: Agent, conversation_id: str | None = None) -> list[dict[str, Any]]:
    """Build tools_config list from agent's tool links."""
    tools_config: list[dict[str, Any]] = []
    # get_agent_with_tools가 attach한 per-user default connection map. prefetch
    # 경로를 우회한 호출자(테스트 등)를 위해 getattr fallback.
    default_connection_map: dict[str, Connection] = getattr(
        agent, "_default_connection_map", {}
    )

    for link in agent.tool_links:
        tool = link.tool
        mcp_server_url: str | None = None
        # auth_config는 executor의 _AuthInjectorInterceptor가 매 tool call의
        # arguments에 주입하는 대상이므로 transport 헤더를 여기 넣으면 안 된다
        # (Codex 6차 adversarial P1). 헤더는 별도 top-level key로 전달.
        mcp_transport_headers: dict[str, str] | None = None

        if tool.type == ToolType.MCP:
            # 우선순위: connection 경유 (M2+) → mcp_server legacy fallback.
            # 둘 다 없는 MCP tool은 실행 불가이므로 빈 auth로 넘기고 URL 키를
            # 생략 → executor가 `tc.get("mcp_server_url")` 체크에서 스킵.
            if tool.connection_id is not None and tool.connection is not None:
                conn = tool.connection
                # Cross-tenant credential leak 방어: DML/이관 실수로 user_id
                # 불일치가 생겨도 런타임에서 타 유저 credential 복호화를 거부.
                assert_connection_ownership(
                    tool_user_id=tool.user_id,
                    connection_user_id=conn.user_id,
                    connection_id=conn.id,
                    tool_name=tool.name,
                )
                assert_credential_ownership(
                    connection_user_id=conn.user_id,
                    credential=conn.credential,
                    connection_id=conn.id,
                )
                extra = conn.extra_config or {}
                url = extra.get("url")
                if not url:
                    raise ToolConfigError(
                        f"MCP tool '{tool.name}' connection {conn.id} is "
                        "missing extra_config.url"
                    )
                cred_auth = resolve_env_vars(
                    extra.get("env_vars"),
                    conn.credential,
                    context={
                        "connection_id": str(conn.id),
                        "tool_name": tool.name,
                    },
                )
                # ConnectionExtraConfig.headers는 transport 헤더 — auth_config에
                # 병합하지 말고 별도 필드로 executor에 전달해야 tool argument
                # injection 대상에서 제외된다.
                extra_headers = extra.get("headers")
                if extra_headers:
                    mcp_transport_headers = extra_headers
                mcp_server_url = url
            elif tool.mcp_server is not None:
                # Legacy fallback — M3~M6 이행기 동안 기존 mcp_servers 경로 유지.
                # **M6.1에서 제거** (옵션 D — PATCH /api/tools/{id} connection_id
                # 도입 후 tools.mcp_server_id drop과 함께). credential_service.
                # resolve_server_auth가 credential 우선 + inline auth_config
                # fallback 규칙을 이미 구현 (test_mcp_connection router도 동일
                # 함수 경유 → runtime 정합).
                cred_auth = resolve_server_auth(tool.mcp_server) or {}
                mcp_server_url = tool.mcp_server.url
            else:
                cred_auth = {}
        elif tool.type == ToolType.PREBUILT:
            # PREBUILT 도구: provider_name 있으면 per-user default connection
            # 경유. provider_name NULL row는 m10 백필로 이미 해소됨 — M6 이후
            # env fallback과 동치이므로 `{}` 반환.
            if tool.provider_name:
                cred_auth = _resolve_prebuilt_auth(tool, default_connection_map)
            else:
                cred_auth = {}
        elif tool.type == ToolType.CUSTOM:
            # CUSTOM 도구: connection_id FK 경유 → credential 복호화 +
            # 3-state fail-closed (M6에서 legacy fallback 제거).
            cred_auth = _resolve_custom_auth(tool)
        else:
            # BUILTIN 등 나머지. env fallback도 connection도 없는 도구 —
            # auth는 빈 dict (M6에서 legacy credential/auth_config 컬럼 제거).
            cred_auth = {}

        config_entry: dict[str, Any] = {
            "type": tool.type,
            "name": tool.name,
            "description": tool.description,
            "api_url": tool.api_url,
            "http_method": tool.http_method,
            "parameters_schema": tool.parameters_schema,
            "auth_type": tool.auth_type,
            "auth_config": cred_auth or None,
        }
        if tool.type == ToolType.MCP and mcp_server_url is not None:
            config_entry["mcp_server_url"] = mcp_server_url
            config_entry["mcp_tool_name"] = tool.name
            if mcp_transport_headers:
                config_entry["mcp_transport_headers"] = mcp_transport_headers
        tools_config.append(config_entry)

    return tools_config
