from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shlex
import sys
import time
import uuid as _uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.middleware_registry import (
    DEEPAGENT_BUILTIN_TYPES,
    build_middleware_instances,
    get_provider_middleware,
)
from app.agent_runtime.model_factory import create_chat_model
from app.agent_runtime.streaming import stream_agent_response
from app.agent_runtime.tool_factory import create_tool_for_runtime
from app.agent_runtime.tools.ask_user import ask_user as ask_user_tool
from app.hooks import HookContext, HookResult, hooks

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# HiTL: interrupt_on 자동 생성 시 쓰기/실행 도구만 대상으로 하는 키워드
_WRITE_TOOL_KEYWORDS = frozenset(
    {
        "book",
        "create",
        "send",
        "delete",
        "update",
        "write",
        "execute",
        "reserve",
    }
)


@dataclass
class AgentConfig:
    """에이전트 실행에 필요한 설정 묶음. executor 공용 함수들의 시그니처를 단순화."""

    provider: str
    model_name: str
    api_key: str | None
    base_url: str | None
    system_prompt: str
    tools_config: list[dict[str, Any]]
    thread_id: str
    model_params: dict[str, Any] | None = None
    middleware_configs: list[dict[str, Any]] | None = None
    agent_skills: list[dict[str, Any]] | None = None
    agent_id: str | None = None
    provider_api_keys: dict[str, str | None] | None = None
    cost_per_input_token: float | None = None
    cost_per_output_token: float | None = None
    # Hook framework correlation — populated by router/trigger executor.
    user_id: str | None = None
    model_id: str | None = None
    llm_credential_id: str | None = None
    # Optional ordered fallback chain. Each entry is
    # ``{"provider": str, "model_name": str, "base_url": str | None,
    #   "model_id": str | None}`` and is tried in order when the primary
    # ``create_chat_model`` raises a recoverable error. Resolved by the
    # caller (chat_service / trigger_executor) so the executor stays free of
    # DB dependencies.
    model_fallback_chain: list[dict[str, Any]] | None = None
    # M-CHAT1b — when set, agent runs are forked off this LangGraph checkpoint
    # (used by edit / regenerate to branch off an earlier message instead of
    # appending to the thread tip).
    checkpoint_id: str | None = None


def _create_skill_execute_tool(output_dir: Path, thread_id: str = "") -> BaseTool:
    """스킬 디렉토리에서 Python 스크립트를 실행하는 도구를 생성."""

    api_file_prefix = f"/api/conversations/{thread_id}/files/" if thread_id else ""
    _path_re = re.compile(re.escape(str(output_dir)) + r"/([^\s\n]+)") if api_file_prefix else None

    async def execute_in_skill(skill_directory: str, command: str) -> str:
        """스킬 디렉토리에서 Python 스크립트를 실행합니다.

        Args:
            skill_directory: 스킬 디렉토리의 가상 경로 (예: /skills/146ecc62.../)
            command: 실행할 명령어 (예: python scripts/mark_seat.py search 이상윤)
        """
        resolved = (_DATA_DIR / skill_directory.strip("/")).resolve()
        if not resolved.is_relative_to(_DATA_DIR.resolve()) or not resolved.is_dir():
            return f"Error: invalid skill directory: {skill_directory}"

        args = shlex.split(command)
        if not args or args[0] != "python":
            return "Error: only python commands are allowed."

        # 스크립트 경로가 스킬 디렉토리 하위인지 검증
        if len(args) > 1 and not args[1].startswith("-"):
            script_path = (resolved / args[1]).resolve()
            if not script_path.is_relative_to(resolved):
                return "Error: script must be within the skill directory."

        args[0] = sys.executable

        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        out = str(output_dir)
        env = {
            "PATH": "/usr/bin:/usr/local/bin",
            "PYTHONPATH": str(resolved),
            "HOME": str(resolved),
            "SKILL_OUTPUT_DIR": out,
            "OUTPUTS_DIR": out,
        }

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(resolved),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return "Error: script execution timed out (30s)."

        result = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            result += f"\nSTDERR: {err}"

        # IMAGE: 절대경로 → API URL 자동 변환
        if _path_re and str(output_dir) in result:
            result = _path_re.sub(lambda m: api_file_prefix + m.group(1), result)

        # 출력 파일 수집
        def _collect_outputs() -> list[str]:
            if output_dir.exists():
                return [f.name for f in output_dir.iterdir() if f.is_file()]
            return []

        files = await asyncio.to_thread(_collect_outputs)
        if files:
            result += "\n\nOUTPUT_FILES: " + ", ".join(files)

        return result

    return StructuredTool.from_function(
        coroutine=execute_in_skill,
        name="execute_in_skill",
        description=(
            "Execute a Python script inside a skill directory. "
            "Use this when SKILL.md instructs you to run a script. "
            "Output files (images etc.) will be in OUTPUT_FILES."
        ),
    )


def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    *,
    middleware: list | None = None,
    interrupt_on: dict[str, Any] | bool | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    backend: Any | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    name: str | None = None,
) -> Any:
    """Build a moldy agent. Returns CompiledStateGraph."""
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware or (),
        interrupt_on=interrupt_on,  # type: ignore[arg-type]  # bool/dict 양쪽 지원
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        skills=skills,
        memory=memory,
        name=name,
    )


# ---------------------------------------------------------------------------
# MCP tool helpers — langchain-mcp-adapters
# ---------------------------------------------------------------------------


def _auth_config_to_headers(auth_config: dict[str, str] | None) -> dict[str, str]:
    """auth_config를 HTTP 헤더로 변환."""
    if not auth_config:
        return {}
    if "headers" in auth_config:
        return auth_config["headers"]  # type: ignore[return-value]  # legacy: dict 형태 전달 시
    return {}


def _url_to_server_key(url: str) -> str:
    """MCP 서버 URL을 고유 키로 변환 (호스트 + 경로 포함)."""
    parsed = urlparse(url)
    key = parsed.netloc + parsed.path.rstrip("/")
    return key.replace(".", "_").replace(":", "_").replace("/", "_")


class _AuthInjectorInterceptor:
    """MCP 도구 호출 시 auth_config 값을 arguments에 자동 주입.

    langchain-mcp-adapters의 ToolCallInterceptor 프로토콜을 구현.
    MCP 서버로 JSON-RPC tools/call 전송 직전에 request.args를 수정한다.
    """

    def __init__(self, tool_auth: dict[str, dict]) -> None:
        self.tool_auth = tool_auth  # tool_name → auth_config

    async def __call__(self, request: Any, handler: Any) -> Any:
        auth = self.tool_auth.get(request.name)
        if auth:
            merged = {**auth, **request.args}
            request = request.override(args=merged)
        return await handler(request)


def _hide_auth_params_from_schema(tool: BaseTool, auth_keys: set[str]) -> None:
    """도구의 dict 스키마에서 auth 파라미터를 제거하여 LLM에게 숨김."""
    schema = tool.args_schema
    if not isinstance(schema, dict) or not auth_keys:
        return
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for key in auth_keys:
        props.pop(key, None)
    schema["required"] = [r for r in required if r not in auth_keys]


def _create_mcp_error_stub(name: str) -> BaseTool:
    """MCP 서버 연결 실패 시 에러를 반환하는 stub 도구."""

    async def _call(**kwargs: Any) -> str:
        return f"MCP tool '{name}' is temporarily unavailable. Please try again later."

    return StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=f"MCP tool (currently unavailable): {name}",
    )


async def _build_mcp_tools(mcp_configs: list[dict]) -> list[BaseTool]:
    """MCP 도구를 langchain-mcp-adapters로 생성."""
    if not mcp_configs:
        return []

    from langchain_mcp_adapters.client import MultiServerMCPClient

    # 1. MCP 서버 (URL, transport headers)별로 그룹화 — 같은 URL이어도 다른
    # 연결이 다른 헤더(X-Tenant 등)를 쓸 수 있으므로 URL만으로 묶으면 멀티
    # 테넌트 MCP gateway에서 cross-tenant 헤더 혼선이 발생한다 (Codex 7차
    # adversarial P2). 헤더 조합도 함께 키로 사용해 분리.
    servers: dict[str, dict] = {}
    tool_filter: dict[str, set[str]] = {}  # server_key → {tool_names}
    tool_auth: dict[str, dict] = {}  # tool_name → auth_config

    for tc in mcp_configs:
        url = tc["mcp_server_url"]
        tool_name = tc.get("mcp_tool_name", tc["name"])
        # transport 헤더는 `mcp_transport_headers`(신규 경로, connection
        # 경유) 우선 사용. legacy auth_config["headers"]도 fallback.
        headers = tc.get("mcp_transport_headers") or _auth_config_to_headers(tc.get("auth_config"))
        # 정렬된 JSON 직렬화의 SHA256 단축 해시 — process 재시작 후에도 같은
        # (url, headers) 조합이 같은 key/이름 prefix를 생성하도록 deterministic
        # 사용. `hash()`는 PYTHONHASHSEED 때문에 process-randomized라 HiTL
        # resume 시 tool name이 바뀜 (Codex 8차 adversarial F2).
        headers_digest = hashlib.sha256(
            json.dumps(headers or {}, sort_keys=True).encode()
        ).hexdigest()[:8]
        key = f"{_url_to_server_key(url)}|{headers_digest}"

        if key not in servers:
            servers[key] = {
                "transport": "streamable_http",
                "url": url,
                "headers": headers or None,
            }
            tool_filter[key] = set()

        tool_filter[key].add(tool_name)

        auth = tc.get("auth_config")
        if auth:
            tool_auth[tool_name] = auth

    # auth 파라미터 키 수집 (스키마에서 숨길 대상)
    auth_param_keys: set[str] = set()
    for auth in tool_auth.values():
        auth_param_keys.update(auth.keys())

    # interceptor: MCP tools/call 직전에 auth 값을 arguments에 주입
    interceptors = [_AuthInjectorInterceptor(tool_auth)] if tool_auth else None

    # 2. 서버별로 도구 로딩 + 필터링 — (tool, origin) 쌍으로 추적
    collected: list[tuple[BaseTool, str]] = []

    from app.config import settings as _settings

    for key, config in servers.items():
        try:
            client = MultiServerMCPClient(
                {key: config},  # type: ignore[arg-type]  # dict는 Connection TypedDict 호환
                tool_interceptors=interceptors,  # type: ignore[arg-type]
            )
            server_tools = await asyncio.wait_for(
                client.get_tools(),
                timeout=_settings.mcp_connection_timeout,
            )
            needed = tool_filter[key]
            for t in server_tools:
                if t.name in needed:
                    _hide_auth_params_from_schema(t, auth_param_keys)
                    collected.append((t, key))
        except Exception:
            logger.warning("MCP tool loading failed for %s", key, exc_info=True)
            for tool_name in tool_filter[key]:
                collected.append((_create_mcp_error_stub(tool_name), key))

    # 3. 중복 이름 disambiguation — 서버 키를 prefix로 추가
    name_counts: dict[str, int] = {}
    for tool, _ in collected:
        name_counts[tool.name] = name_counts.get(tool.name, 0) + 1

    if any(c > 1 for c in name_counts.values()):
        for tool, origin in collected:
            if name_counts.get(tool.name, 0) > 1:
                tool.name = f"{origin}_{tool.name}"

    return [tool for tool, _ in collected]


_MIDDLEWARE_MODEL_FIELDS = frozenset({"model", "fallback_model"})


def _resolve_middleware_model_params(
    configs: list[dict[str, Any]],
    provider_api_keys: dict[str, str | None],
) -> list[dict[str, Any]]:
    """미들웨어 config의 model 문자열을 BaseChatModel 객체로 사전 해석.

    provider_api_keys에 해당 프로바이더 키가 없으면 api_key=None 전달
    → LangChain env var 폴백 차단, 인증 실패로 안전하게 처리.
    """
    resolved = []
    for config in configs:
        params = dict(config.get("params", {}))
        for field_name in _MIDDLEWARE_MODEL_FIELDS:
            val = params.get(field_name)
            if isinstance(val, str) and ":" in val:
                prov, mname = val.split(":", 1)
                params[field_name] = create_chat_model(
                    prov, mname, api_key=provider_api_keys.get(prov)
                )
        resolved.append({**config, "params": params})
    return resolved


def _build_model_with_fallback(cfg: AgentConfig) -> BaseChatModel:
    """Construct the primary chat model, walking ``model_fallback_chain``
    when the primary raises a recoverable error.

    This mirrors :func:`app.agent_runtime.model_factory.create_chat_model_with_fallback`
    but operates on the pre-resolved chain in ``AgentConfig`` so the executor
    can stay synchronous and DB-free. The chain entries are resolved by the
    caller (chat_service / trigger_executor) which has the DB session.
    """

    from app.agent_runtime.model_factory import _is_fallback_recoverable

    last_error: BaseException | None = None
    chain: list[dict[str, Any]] = []
    chain.append(
        {
            "provider": cfg.provider,
            "model_name": cfg.model_name,
            "base_url": cfg.base_url,
        }
    )
    for entry in cfg.model_fallback_chain or []:
        chain.append(entry)

    for idx, entry in enumerate(chain):
        try:
            return create_chat_model(
                entry["provider"],
                entry["model_name"],
                cfg.api_key,
                entry.get("base_url"),
                **(cfg.model_params or {}),
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if idx == len(chain) - 1 or not _is_fallback_recoverable(exc):
                raise
            logger.info(
                "model %s/%s failed; trying fallback (%d remaining)",
                entry["provider"],
                entry["model_name"],
                len(chain) - idx - 1,
            )

    # Unreachable: the loop either returns or re-raises, but mypy isn't sure.
    assert last_error is not None
    raise last_error


async def _prepare_agent(
    cfg: AgentConfig,
    *,
    messages_history: list[dict[str, str]],
    is_trigger_mode: bool = False,
) -> tuple[Any, list, dict]:
    """에이전트 빌드 + 설정. stream/invoke 공용.

    ``is_trigger_mode=True`` 는 트리거(invoke) 모드 indicator — 사용자가 없으므로
    (a) ``ask_user`` 도구 미주입(호출 시 영원히 hang), (b) HiTL ``interrupt_on``
    을 None 으로 강제 override 하여 위험 도구 승인 게이트도 자동 통과.
    """
    system_prompt = cfg.system_prompt
    model = _build_model_with_fallback(cfg)

    # 1. 도구 생성 — 단일 경로 (definition_key + credentials).
    # MCP는 향후 별도 mcp_configs 키로 전달 (PoC 단계에서는 비어있음).
    langchain_tools: list[BaseTool] = []
    mcp_configs: list[dict] = []

    for tc in cfg.tools_config:
        if tc.get("mcp_server_url"):
            # 임시 호환: 옛 chat_service가 채워주던 MCP 항목. M5 이후로는
            # build_tools_config가 더 이상 채우지 않으므로 dead branch에 가깝지만
            # 외부 호출자가 직접 cfg를 만들 수 있으므로 무해하게 보존.
            mcp_configs.append(tc)
            continue
        tool = create_tool_for_runtime(tc)
        if tool is not None:
            langchain_tools.append(tool)

    # 2. MCP 도구 — langchain-mcp-adapters 사용 (legacy 경로, 항목이 비면 no-op)
    mcp_tools = await _build_mcp_tools(mcp_configs)
    langchain_tools.extend(mcp_tools)

    # 3. 미들웨어 — deepagents 빌트인 타입 제외 후, model 문자열을 BaseChatModel로 사전 해석
    filtered_mw = [
        c for c in (cfg.middleware_configs or []) if c.get("type") not in DEEPAGENT_BUILTIN_TYPES
    ]
    resolved_mw = _resolve_middleware_model_params(filtered_mw, cfg.provider_api_keys or {})
    middleware = build_middleware_instances(resolved_mw)
    middleware += get_provider_middleware(cfg.provider)

    # 3-1. HiTL — interrupt_on 추출 후 표준 미들웨어 명시 인스턴스화
    # interrupt_on은 dict[str, bool | InterruptOnConfig] 형식.
    # 주의: 모든 도구에 interrupt를 걸면 LLM이 도구 호출을 피할 수 있음.
    # 따라서 params에 명시적 interrupt_on이 없으면 부작용(side-effect) 가능성이 있는
    # 쓰기/실행 도구만 대상으로 함 (모듈 레벨 _WRITE_TOOL_KEYWORDS 참조).
    interrupt_on: dict[str, Any] | None = None
    for mw_config in cfg.middleware_configs or []:
        if mw_config.get("type") == "human_in_the_loop":
            explicit = mw_config.get("params", {}).get("interrupt_on")
            if isinstance(explicit, dict) and explicit:
                interrupt_on = explicit
            else:
                # params에 interrupt_on이 없으면 쓰기/실행 도구만 interrupt
                interrupt_on = {
                    t.name: True
                    for t in langchain_tools
                    if any(kw in t.name.lower() for kw in _WRITE_TOOL_KEYWORDS)
                }
                # 대상 도구가 없으면 None (interrupt 비활성)
                if not interrupt_on:
                    interrupt_on = None
            break

    # 3-2. 트리거 모드 강제 차단 — invoke 경로에서 HiTL interrupt 가 발생하면
    # 사용자가 없어 hang.
    if is_trigger_mode:
        interrupt_on = None

    # 3-2b. ask_user 표준 wire wrap (ADR-012 §1 옵션 A) — 자체 ask_user.py 의
    # native interrupt 가 표준 미들웨어로 wrap 되도록 interrupt_on 에 등록.
    if interrupt_on is not None and any(t.name == "ask_user" for t in langchain_tools):
        interrupt_on.setdefault("ask_user", {"allowed_decisions": ["respond"]})

    # 3-3. HumanInTheLoopMiddleware 명시 인스턴스화 (ADR-012 Phase 1).
    # deepagents.create_deep_agent(interrupt_on=...) 자동 주입 대신, 본 executor
    # 가 명시적으로 인스턴스를 미들웨어 list 에 합쳐 deep agent 에 전달한다.
    # 이로써 (a) registry 단일 경로, (b) description_prefix 등 옵션 제어 가능,
    # (c) 디버깅/회귀 가드 추적 용이. 자동 주입과 명시 주입은 둘 중 하나만 —
    # 동시에 하면 미들웨어 중복 등록되므로 build_agent(interrupt_on=None) 으로
    # 자동 주입 비활성화한다.
    if interrupt_on:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))

    # 4. Backend + Skills + Memory 구성
    backend = FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)

    skills_sources: list[str] | None = None
    if cfg.agent_skills:
        skills_sources = ["/skills/"]
        # 스킬 스크립트 실행 도구 추가 — 출력은 대화 세션 폴더에 저장 (절대경로)
        conv_output_dir = (_DATA_DIR / "conversations" / cfg.thread_id).resolve()
        langchain_tools.append(_create_skill_execute_tool(conv_output_dir, cfg.thread_id))
        system_prompt += (
            "\n\n## 스킬 사용 규칙\n"
            "스킬을 사용할 때는 반드시 read_file 도구로 SKILL.md를 먼저 읽고 "
            "그 안의 지시를 직접 따르세요. "
            "스크립트 실행이 필요하면 execute_in_skill 도구를 사용하세요. "
            "task 도구의 subagent_type에 스킬 이름을 넣지 마세요. "
            "유일하게 허용된 subagent_type은 'general-purpose'입니다.\n"
            "스크립트 실행 후 OUTPUT_FILES에 이미지가 있으면 "
            "![image](/api/conversations/" + cfg.thread_id + "/files/<파일명>) 형식으로 표시하세요."
        )

        # LambChat-style "Available Skills" listing — gives the model the
        # name/description/slug for each attached skill so it knows which
        # SKILL.md to open via read_file.
        from app.skills.prompt import build_skills_prompt

        skills_block = build_skills_prompt(cfg.agent_skills)
        if skills_block:
            system_prompt += "\n" + skills_block

    memory_sources: list[str] | None = None
    if cfg.agent_id:
        (_DATA_DIR / "agents" / cfg.agent_id).mkdir(parents=True, exist_ok=True)
        memory_sources = [f"/agents/{cfg.agent_id}/AGENTS.md"]

    # 4-1. ask_user 도구 — 대화형(스트리밍) 에이전트에만 포함
    # 트리거/배치 실행 시에는 사용자가 없으므로 제외
    if not is_trigger_mode:
        langchain_tools.append(ask_user_tool)

    # 5. 에이전트 빌드 — create_deep_agent + checkpointer
    from app.agent_runtime.checkpointer import get_checkpointer

    agent = build_agent(
        model,
        langchain_tools,
        system_prompt,
        middleware=middleware or None,
        # ADR-012 Phase 1: 명시 인스턴스 사용 — deepagents 자동 주입 회피.
        # HumanInTheLoopMiddleware 는 위에서 middleware list 에 직접 추가됨.
        interrupt_on=None,
        checkpointer=get_checkpointer(),
        backend=backend,
        skills=skills_sources,
        memory=memory_sources,
        name=f"agent_{cfg.thread_id[:8]}",
    )

    lc_messages = convert_to_langchain_messages(messages_history)
    config: dict[str, Any] = {"configurable": {"thread_id": cfg.thread_id}}
    if cfg.checkpoint_id:
        # LangGraph time-travel: invoking with an explicit checkpoint_id forks
        # a new branch from that point. The new run's checkpoints chain back to
        # this id, and `alist` reveals both branches as siblings of the parent.
        config["configurable"]["checkpoint_id"] = cfg.checkpoint_id

    return agent, lc_messages, config


def _hook_ctx_for_agent(cfg: AgentConfig) -> HookContext | None:
    """Build a ``HookContext`` for an ``agent_invoke`` call.

    Returns ``None`` when the caller didn't propagate a ``user_id`` (legacy
    tests that build ``AgentConfig`` directly). Hook dispatch is a no-op in
    that case so the runtime stays backward compatible.
    """

    if not cfg.user_id:
        return None
    try:
        user_uuid = _uuid.UUID(str(cfg.user_id))
    except (TypeError, ValueError):
        return None

    metadata: dict[str, Any] = {
        "provider": cfg.provider,
        "model_name": cfg.model_name,
        "thread_id": cfg.thread_id,
    }
    return HookContext(
        request_id=str(_uuid.uuid4()),
        kind="agent_invoke",
        user_id=user_uuid,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        agent_id=_uuid.UUID(cfg.agent_id) if cfg.agent_id else None,
        model_id=_uuid.UUID(cfg.model_id) if cfg.model_id else None,
        credential_id=(
            _uuid.UUID(cfg.llm_credential_id) if cfg.llm_credential_id else None
        ),
        metadata=metadata,
    )


def _hook_result_from_usage(
    duration_ms: int, usage_sink: dict[str, Any]
) -> HookResult:
    """Build a :class:`HookResult` from streaming-captured usage.

    Streaming surfaces ``prompt_tokens`` / ``completion_tokens`` /
    ``estimated_cost`` keys; the hook framework maps them to its own
    ``tokens_in`` / ``tokens_out`` / ``cost_usd`` field names.
    """

    prompt = usage_sink.get("prompt_tokens")
    completion = usage_sink.get("completion_tokens")
    cost = usage_sink.get("estimated_cost")
    return HookResult(
        duration_ms=duration_ms,
        tokens_in=int(prompt) if prompt is not None else None,
        tokens_out=int(completion) if completion is not None else None,
        cost_usd=float(cost) if cost is not None else None,
    )


async def _run_agent_stream(
    cfg: AgentConfig,
    *,
    messages_history: list[dict[str, str]],
    stream_input: Any,
    hook_metadata_extra: dict[str, Any] | None = None,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """공용 stream runner — execute/resume의 prep + hook + 예외 처리 통합 (P0-B).

    - ``messages_history``는 ``_prepare_agent``에 전달 (lc_messages 변환에만 사용).
    - ``stream_input``은 ``stream_agent_response``에 전달할 입력. ``None`` 이면
      execute_agent_stream가 변환한 lc_messages를 그대로 쓴다 (즉 execute는
      stream_input=None 또는 명시 list, resume은 ``Command(resume=...)``).
    - ``hook_metadata_extra``: HookContext.metadata에 추가로 머지(resume용).
    - ``broker`` / ``persist_callback`` / ``run_id`` (W3-out M2): SSE dual-write
      + partial flush 파이프라인. router가 EventBroker, fresh-session-bound
      append_events 콜백, run_id(=assistant_msg_id) 를 주입.
    """

    agent, lc_messages, config = await _prepare_agent(
        cfg,
        messages_history=messages_history,
    )

    # stream_input이 ``_USE_PREPPED_LC_MESSAGES`` sentinel이면 변환된 lc_messages를
    # 그대로 입력으로 사용 (execute path). 빈 리스트는 None으로 폴백 — LangGraph
    # time-travel resume 모드.
    actual_input = stream_input
    if actual_input is _USE_PREPPED_LC_MESSAGES:
        actual_input = lc_messages if lc_messages else None

    ctx = _hook_ctx_for_agent(cfg)
    if ctx is not None:
        if hook_metadata_extra:
            ctx.metadata.update(hook_metadata_extra)
        await hooks.run_pre(ctx)
    started = time.monotonic()
    usage_sink: dict[str, Any] = {}

    try:
        async for chunk in stream_agent_response(
            agent,
            actual_input,
            config,
            cost_per_input_token=cfg.cost_per_input_token,
            cost_per_output_token=cfg.cost_per_output_token,
            usage_sink=usage_sink,
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id=run_id,
        ):
            yield chunk
    except Exception as exc:
        if ctx is not None:
            await hooks.run_failure(ctx, exc)
        raise
    if ctx is not None:
        await hooks.run_post(
            ctx,
            _hook_result_from_usage(
                int((time.monotonic() - started) * 1000), usage_sink
            ),
        )


# Sentinel that tells ``_run_agent_stream`` to feed its prepped lc_messages
# straight into ``stream_agent_response`` (execute path). Resume path passes a
# concrete ``Command(resume=...)`` instead.
_USE_PREPPED_LC_MESSAGES: Any = object()


async def execute_agent_stream(
    cfg: AgentConfig,
    messages_history: list[dict[str, str]],
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """스트리밍 실행 (채팅용).

    빈 ``messages_history``는 LangGraph time-travel resume 모드 — 새 입력
    없이 ``cfg.checkpoint_id`` 시점 state에서 그래프를 다시 돌린다.
    Regenerate가 부모 user 메시지를 중복 주입하지 않고 새 assistant sibling
    만 만들어내는 데 사용한다.

    ``trace_sink`` (W5): 호출자가 list를 넘기면 emit된 SSE 이벤트 dict가 차곡
    차곡 누적된다. 스트림 종료 시점에 caller가 ``trace_storage.record_turn``
    으로 영속화.

    ``broker`` / ``persist_callback`` / ``run_id`` (W3-out M2): GET resume
    파이프라인. router가 주입하면 dual-write + partial flush 활성화.
    """

    async for chunk in _run_agent_stream(
        cfg,
        messages_history=messages_history,
        stream_input=_USE_PREPPED_LC_MESSAGES,
        trace_sink=trace_sink,
        msg_id_sink=msg_id_sink,
        broker=broker,
        persist_callback=persist_callback,
        run_id=run_id,
    ):
        yield chunk


async def resume_agent_stream(
    cfg: AgentConfig,
    resume_value: Any,
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """인터럽트 재개 스트리밍 (HiTL resume)."""
    from langgraph.types import Command

    async for chunk in _run_agent_stream(
        cfg,
        messages_history=[],
        stream_input=Command(resume=resume_value),
        hook_metadata_extra={"resume": True},
        trace_sink=trace_sink,
        msg_id_sink=msg_id_sink,
        broker=broker,
        persist_callback=persist_callback,
        run_id=run_id,
    ):
        yield chunk


async def execute_agent_invoke(
    cfg: AgentConfig,
    messages_history: list[dict[str, str]],
) -> str:
    """비스트리밍 실행 (트리거용). 최종 응답 텍스트만 반환."""
    agent, lc_messages, config = await _prepare_agent(
        cfg,
        messages_history=messages_history,
        is_trigger_mode=True,
    )

    ctx = _hook_ctx_for_agent(cfg)
    if ctx is not None:
        await hooks.run_pre(ctx)
    started = time.monotonic()

    try:
        result = await agent.ainvoke({"messages": lc_messages}, config=config)
    except Exception as exc:
        if ctx is not None:
            await hooks.run_failure(ctx, exc)
        raise

    messages = result.get("messages", [])
    text = ""
    if messages and hasattr(messages[-1], "content"):
        text = messages[-1].content

    if ctx is not None:
        await hooks.run_post(
            ctx,
            HookResult(
                duration_ms=int((time.monotonic() - started) * 1000),
                output=(text[:200] if isinstance(text, str) else None),
            ),
        )
    return text
