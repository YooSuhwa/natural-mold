from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import sys
import time
import uuid as _uuid
from collections.abc import AsyncGenerator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.middleware_registry import (
    DEEPAGENT_BUILTIN_TYPES,
    build_middleware_instances,
    get_provider_middleware,
)
from app.agent_runtime.model_factory import create_chat_model
from app.agent_runtime.skill_tool_dependencies import build_skill_dependency_tool_configs
from app.agent_runtime.streaming import StreamErrorRecord, stream_agent_response
from app.agent_runtime.temporal import build_temporal_context_prompt
from app.agent_runtime.tool_factory import create_builtin_tool, create_tool_for_runtime
from app.agent_runtime.tools.ask_user import ask_user as ask_user_tool
from app.agent_runtime.tools.memory import build_memory_tools
from app.config import settings
from app.exceptions import AppError
from app.hooks import HookContext, HookResult, hooks
from app.marketplace.skill_runtime import (
    SkillRuntimeDescriptor,
    SkillToolContext,
    build_skill_runtime_context,
    resolve_runtime_credentials,
)
from app.observability.langfuse import LangfuseTraceRecord, build_langfuse_run_context
from app.tools.risk import (
    attach_tool_risk,
    default_deepagents_interrupt_policy,
    execute_in_skill_risk,
    interrupt_policy_for_tool,
    mcp_tool_risk,
    merge_interrupt_policies,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SHELL_DEFAULT_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*?)\}")
_SHELL_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_DEFAULT_SKILL_TIMEOUT_SECONDS = 30.0
_MAX_SKILL_TIMEOUT_SECONDS = 420.0
_TEMPORAL_BUILTIN_TOOL_KEYS = (
    "builtin:current_datetime",
    "builtin:resolve_relative_date",
)


class MiddlewareModelCredentialRequiredError(AppError):
    """Raised when middleware model config has no user-owned provider key."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            code="middleware_model_credential_required",
            message=(
                f"미들웨어 모델({provider})에 사용할 본인의 LLM API 키가 등록되어 있지 않습니다. "
                "/credentials 페이지에서 해당 제공자의 키를 등록하거나 미들웨어 모델 설정을 "
                "변경해주세요."
            ),
            status=422,
        )


def _expand_shell_vars(value: str, *, env: dict[str, str], local_vars: dict[str, str]) -> str:
    """Expand the small shell-var subset used in k-skill curl examples."""

    def lookup(name: str) -> str:
        return local_vars.get(name) or env.get(name, "")

    def replace_default(match: re.Match[str]) -> str:
        name = match.group(1)
        fallback = match.group(2)
        return lookup(name) or fallback

    expanded = _SHELL_DEFAULT_RE.sub(replace_default, value)

    def replace_var(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return lookup(name)

    return _SHELL_VAR_RE.sub(replace_var, expanded)


def _prepare_skill_subprocess_args(
    command: str, *, resolved: Path, env: dict[str, str]
) -> tuple[list[str] | None, str | None]:
    """Convert an LLM-provided command into argv for the allowed executables.

    We intentionally do not run a shell here. k-skill docs frequently use
    ``BASE=...`` followed by ``curl "${BASE}/..."``; supporting that narrow
    shape is enough without opening arbitrary shell command execution.
    """

    try:
        raw_args = [arg for arg in shlex.split(command) if arg.strip()]
    except ValueError as exc:
        return None, f"Error: invalid command syntax: {exc}"

    local_vars: dict[str, str] = {}
    index = 0
    while index < len(raw_args) and _SHELL_ASSIGNMENT_RE.match(raw_args[index]):
        name, raw_value = raw_args[index].split("=", 1)
        local_vars[name] = _expand_shell_vars(raw_value, env=env, local_vars=local_vars)
        index += 1

    args = [_expand_shell_vars(arg, env=env, local_vars=local_vars) for arg in raw_args[index:]]
    if not args:
        return None, "Error: command must start with python or curl."

    executable = Path(args[0]).name
    if executable == "python":
        # 스크립트 경로가 스킬 디렉토리 하위인지 검증
        if len(args) > 1 and not args[1].startswith("-"):
            script_path = (resolved / args[1]).resolve()
            if not script_path.is_relative_to(resolved):
                return None, "Error: script must be within the skill directory."
        args[0] = sys.executable
        return args, None

    if executable == "curl":
        args[0] = "curl"
        return args, None

    return None, "Error: only python or curl commands are allowed."


def _skill_timeout_seconds(descriptor: SkillRuntimeDescriptor) -> float:
    profile = descriptor.execution_profile or {}
    raw = profile.get("timeout_seconds")
    if raw is None:
        return _DEFAULT_SKILL_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_SKILL_TIMEOUT_SECONDS
    if seconds <= 0:
        return _DEFAULT_SKILL_TIMEOUT_SECONDS
    return min(seconds, _MAX_SKILL_TIMEOUT_SECONDS)


@dataclass
class AgentConfig:
    """에이전트 실행에 필요한 설정 묶음. executor 공용 함수들의 시그니처를 단순화.

    Multi-user (ADR-016 §6) — 프로덕션 진입점(``routers/conversations.py``,
    ``trigger_executor``)은 ``agent_id`` 와 ``user_id`` 를 항상 함께 채워야
    한다. ``__post_init__`` 가 둘 중 하나만 설정된 경우(특히 ``agent_id`` 는
    있는데 ``user_id`` 가 비어 있는 케이스)를 즉시 ``ValueError`` 로 차단해
    hook framework / 권한 트레이싱이 silently None 으로 떨어지지 않도록 한다.
    DB-free 단위 테스트는 두 필드 모두 비워두면 종전처럼 통과한다.
    """

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
    agent_name: str | None = None
    provider_api_keys: dict[str, str | None] | None = None
    cost_per_input_token: float | None = None
    cost_per_output_token: float | None = None
    # Hook framework correlation — populated by router/trigger executor.
    # NOTE: ``agent_id`` 가 설정되면 반드시 ``user_id`` 도 설정되어야 한다
    # (``__post_init__`` 가드). 일치하지 않으면 ValueError.
    user_id: str | None = None
    model_id: str | None = None
    llm_credential_id: str | None = None
    agent_owner_user_id: str | None = None
    caller_user_id: str | None = None
    credential_subject_user_id: str | None = None
    identity_mode: str | None = None
    agent_runtime_name: str | None = None
    subagents_config: list[dict[str, Any]] | None = None
    subagent_display_names: dict[str, str] | None = None
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

    def __post_init__(self) -> None:
        # ADR-016 §6 — 프로덕션 callsite(``conversations`` 라우터,
        # ``trigger_executor``)는 ``agent_id`` + ``user_id`` 둘 다 채운다.
        # 한쪽만 채워진 상태로 hook framework 가 호출되면 권한 트레이싱이
        # silently None 으로 떨어져 "누구의 호출인가" 추적이 불가능해진다.
        # 즉시 fail-fast.
        if self.agent_id and not self.user_id:
            raise ValueError(
                "AgentConfig.user_id is required when agent_id is set "
                "(production callsite forgot to propagate authenticated user)."
            )


@dataclass
class RuntimeComponents:
    model_candidates: list[BaseChatModel]
    model: BaseChatModel
    tools: list[BaseTool]
    middleware: list[Any]
    system_prompt: str
    skills_sources: list[str] | None
    backend: Any | None
    memory_sources: list[str] | None
    permissions: list[FilesystemPermission]
    interrupt_on: dict[str, Any] | None


def _create_skill_execute_tool(ctx: SkillToolContext) -> BaseTool:
    """스킬 디렉토리에서 Python 스크립트를 실행하는 도구를 생성.

    ADR-017 Slice E refactor — the tool now closes over a
    ``SkillToolContext`` (output_dir + thread_id + runtime_root + slug
    descriptor map). Stage 1 preserves the legacy validation surface;
    stage 2 swaps ``runtime_root`` to the per-thread location and adds
    "unknown slug" rejection. The closure shape was deliberately
    chosen to avoid an argument explosion on the inner ``execute_in_skill``
    body — see Bezos OI-3.
    """

    output_dir = ctx.output_dir
    api_file_prefix = f"/api/conversations/{ctx.thread_id}/files/" if ctx.thread_id else ""
    _path_re = re.compile(re.escape(str(output_dir)) + r"/([^\s\n]+)") if api_file_prefix else None

    async def execute_in_skill(skill_directory: str, command: str) -> str:
        """스킬 디렉토리에서 Python 스크립트를 실행합니다.

        Args:
            skill_directory: 스킬 디렉토리의 가상 경로
                (예: /runtime/<thread_id>/skills/<slug>/).
            command: 실행할 명령어 (예: python scripts/mark_seat.py search 이상윤)
        """
        # ``Path(skill_directory).name`` extracts the final segment
        # regardless of leading slashes / trailing slashes — the LLM may
        # pass any of ``/skills/<slug>``, ``<slug>``, ``<slug>/``.
        requested_slug = Path(skill_directory.strip("/")).name
        descriptor = ctx.descriptors.get(requested_slug)
        if descriptor is None:
            # Selected-skill mount (Spec §9) — even if the slug resolves
            # to a real on-disk directory, refuse it when it wasn't
            # attached to this agent. This is the regression guard for
            # the legacy broad ``/skills/`` mount that leaked siblings.
            return f"Error: skill not attached to this agent: {requested_slug}"

        # Resolve against the per-thread runtime root so traversal
        # attempts (``../``, absolute paths like ``/etc/passwd``) all
        # fail the ``is_relative_to`` check below.
        resolved = descriptor.runtime_storage_path.resolve()
        if not resolved.is_relative_to(ctx.runtime_root.resolve()) or not resolved.is_dir():
            return f"Error: invalid skill directory: {skill_directory}"

        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        out = str(output_dir)
        env = {
            "PATH": "/usr/bin:/usr/local/bin",
            "PYTHONPATH": str(resolved),
            "HOME": str(resolved),
            "SKILL_OUTPUT_DIR": out,
            "OUTPUTS_DIR": out,
        }
        for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            value = os.environ.get(key)
            if value:
                env[key] = value
        # Slice E Stage 3 — credential env injection (Spec §8.2).
        # ``descriptor.credential_bindings`` is populated by
        # ``build_skill_runtime_context`` at agent build time so the
        # hot path here only does an in-memory copy; no decrypt, no DB.
        # ``env_map`` shape: ``{credential_field_name: env_var_name}``.
        injected_env: dict[str, str] = {}
        for rc in descriptor.credential_bindings.values():
            for field, env_name in rc.env_map.items():
                value = rc.decrypted.get(field)
                if value is None:
                    continue
                env[env_name] = value
                injected_env[env_name] = value

        args, error = _prepare_skill_subprocess_args(command, resolved=resolved, env=env)
        if error is not None or args is None:
            return error or "Error: invalid command."

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(resolved),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        timeout_seconds = _skill_timeout_seconds(descriptor)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Error: script execution timed out ({timeout_seconds:g}s)."

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

        # Slice E Stage 4 — redact credential values that the script
        # may have echoed back through stdout/stderr (debug prints,
        # uncaught exceptions, etc.). The mapped env dict is the
        # authoritative set of values the runtime injected for this
        # skill; everything else passes through untouched.
        if injected_env:
            from app.marketplace.redaction import redact_credential_values

            result = redact_credential_values(result, injected_env)

        return result

    tool = StructuredTool.from_function(
        coroutine=execute_in_skill,
        name="execute_in_skill",
        description=(
            "Execute a Python script inside a skill directory. "
            "Use this when SKILL.md instructs you to run a script. "
            "Output files (images etc.) will be in OUTPUT_FILES."
        ),
    )
    return attach_tool_risk(tool, execute_in_skill_risk())


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
    permissions: list[FilesystemPermission] | None = None,
    name: str | None = None,
    subagents: list[dict[str, Any]] | None = None,
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
        permissions=permissions,
        name=name,
        subagents=subagents,
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

    tool = StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=f"MCP tool (currently unavailable): {name}",
    )
    return attach_tool_risk(tool, mcp_tool_risk(name))


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
    tool_configs: dict[tuple[str, str], dict] = {}  # (server_key, tool_name) → runtime config

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
        tool_configs[(key, tool_name)] = tc

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

    from app.agent_runtime.mcp_cache import MCPToolWithRetry, get_cached_mcp_tools
    from app.config import settings as _settings

    for key, config in servers.items():
        try:

            async def _load_server_tools(
                *,
                cache_key: str = key,
                server_config: dict[str, Any] = config,
            ) -> list[BaseTool]:
                client = MultiServerMCPClient(
                    {cache_key: server_config},  # type: ignore[arg-type]  # dict는 Connection TypedDict 호환
                    tool_interceptors=interceptors,  # type: ignore[arg-type]
                )
                return await asyncio.wait_for(
                    client.get_tools(),
                    timeout=_settings.mcp_connection_timeout,
                )

            server_tools = await get_cached_mcp_tools(
                key,
                _load_server_tools,
                ttl_seconds=max(1.0, float(_settings.mcp_connection_timeout) * 30),
            )
            needed = tool_filter[key]
            for t in server_tools:
                if t.name in needed:
                    _hide_auth_params_from_schema(t, auth_param_keys)
                    risk_config = dict(tool_configs.get((key, t.name), {}))
                    risk_config.setdefault("definition_key", "mcp")
                    risk_config.setdefault("name", t.name)
                    risk_config.setdefault("mcp_tool_name", t.name)
                    risk_config.setdefault("mcp_server_url", config.get("url"))
                    if t.description and not risk_config.get("description"):
                        risk_config["description"] = t.description
                    metadata = getattr(t, "metadata", None)
                    wrapped = MCPToolWithRetry(
                        t,
                        max_retries=2,
                        retry_delay=0.25,
                        timeout_seconds=float(_settings.mcp_connection_timeout),
                    )
                    attach_tool_risk(
                        wrapped,
                        mcp_tool_risk(
                            wrapped.name,
                            metadata=metadata if isinstance(metadata, dict) else None,
                            config=risk_config,
                        ),
                    )
                    collected.append((wrapped, key))
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

    User-facing agent runtime must not fall through to env/system credentials.
    The caller provides only user-owned provider keys; missing keys become a
    clear 422 error before LangChain model construction.
    """
    resolved = []
    for config in configs:
        params = dict(config.get("params", {}))
        for field_name in _MIDDLEWARE_MODEL_FIELDS:
            val = params.get(field_name)
            if isinstance(val, str) and ":" in val:
                prov, mname = val.split(":", 1)
                api_key = provider_api_keys.get(prov)
                if not api_key:
                    raise MiddlewareModelCredentialRequiredError(prov)
                params[field_name] = create_chat_model(
                    prov,
                    mname,
                    api_key=api_key,
                    allow_env_fallback=False,
                )
        resolved.append({**config, "params": params})
    return resolved


def _model_constructor_params(cfg: AgentConfig) -> dict[str, Any]:
    params = dict(cfg.model_params or {})
    params.pop("recursion_limit", None)
    return params


def _configured_recursion_limit(cfg: AgentConfig) -> int | None:
    raw = (cfg.model_params or {}).get("recursion_limit")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _model_chain(cfg: AgentConfig) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = [
        {
            "provider": cfg.provider,
            "model_name": cfg.model_name,
            "base_url": cfg.base_url,
        }
    ]
    chain.extend(cfg.model_fallback_chain or [])
    return chain


def _build_model_candidates(cfg: AgentConfig) -> list[BaseChatModel]:
    """Construct the primary chat model, walking ``model_fallback_chain``
    when the primary raises a recoverable error.

    This mirrors :func:`app.agent_runtime.model_factory.create_chat_model_with_fallback`
    but operates on the pre-resolved chain in ``AgentConfig`` so the executor
    can stay synchronous and DB-free. The chain entries are resolved by the
    caller (chat_service / trigger_executor) which has the DB session.
    """

    from app.agent_runtime.model_factory import _is_fallback_recoverable

    last_error: BaseException | None = None
    candidates: list[BaseChatModel] = []
    params = _model_constructor_params(cfg)
    chain = _model_chain(cfg)

    for idx, entry in enumerate(chain):
        try:
            candidates.append(
                create_chat_model(
                    entry["provider"],
                    entry["model_name"],
                    cfg.api_key,
                    entry.get("base_url"),
                    **params,
                )
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if not candidates:
                if idx == len(chain) - 1 or not _is_fallback_recoverable(exc):
                    raise
                logger.info(
                    "model %s/%s failed; trying fallback (%d remaining)",
                    entry["provider"],
                    entry["model_name"],
                    len(chain) - idx - 1,
                )
                continue
            logger.warning(
                "fallback model %s/%s could not be constructed; runtime fallback will skip it",
                entry["provider"],
                entry["model_name"],
                exc_info=True,
            )

    if candidates:
        return candidates
    assert last_error is not None
    raise last_error


def _build_model_with_fallback(cfg: AgentConfig) -> BaseChatModel:
    """Backward-compatible helper that returns the first constructible candidate."""

    return _build_model_candidates(cfg)[0]


def _is_retryable_model_error(exc: Exception) -> bool:
    from app.agent_runtime.model_factory import _is_fallback_recoverable

    if _is_fallback_recoverable(exc):
        return True
    return isinstance(exc, ValueError) and "No generations found in stream" in str(exc)


def _has_visible_ai_content(response: ModelResponse[Any] | AIMessage) -> bool:
    messages = [response] if isinstance(response, AIMessage) else list(response.result)
    for message in messages:
        if getattr(message, "type", None) != "ai":
            continue
        if getattr(message, "tool_calls", None):
            return True
        content = getattr(message, "content", None)
        if isinstance(content, str):
            if content.strip():
                return True
            continue
        if isinstance(content, list):
            for block in content:
                if isinstance(block, str) and block.strip():
                    return True
                if isinstance(block, dict) and str(block.get("text") or "").strip():
                    return True
    return False


class EmptyContentRetryMiddleware(AgentMiddleware):
    """Retry model calls that return an empty assistant message without tool calls."""

    def __init__(self, *, max_retries: int = 1) -> None:
        super().__init__()
        self.max_retries = max(0, max_retries)
        self.tools = []

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        response = None
        for attempt in range(self.max_retries + 1):
            response = handler(request)
            if _has_visible_ai_content(response) or attempt >= self.max_retries:
                return response
        return response

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        response = None
        for attempt in range(self.max_retries + 1):
            response = await handler(request)
            if _has_visible_ai_content(response) or attempt >= self.max_retries:
                return response
        return response


def _build_default_reliability_middleware(
    model_candidates: list[BaseChatModel],
    *,
    configured_types: set[str],
) -> list[Any]:
    from langchain.agents.middleware import ModelFallbackMiddleware, ModelRetryMiddleware

    middleware: list[Any] = []
    if len(model_candidates) > 1:
        middleware.append(ModelFallbackMiddleware(*model_candidates[1:]))
    if "model_retry" not in configured_types:
        middleware.append(
            ModelRetryMiddleware(
                max_retries=2,
                retry_on=_is_retryable_model_error,
                on_failure="error",
                initial_delay=1.0,
                backoff_factor=2.0,
                max_delay=60.0,
                jitter=True,
            )
        )
    middleware.append(EmptyContentRetryMiddleware(max_retries=1))
    return middleware


def _append_temporal_tools(tools: list[BaseTool]) -> None:
    """Ensure date/time grounding tools are always available to agents."""

    existing = {tool.name for tool in tools}
    for key in _TEMPORAL_BUILTIN_TOOL_KEYS:
        tool = create_builtin_tool(key)
        if tool is None or tool.name in existing:
            continue
        tools.append(tool)
        existing.add(tool.name)


def _default_interrupt_on_from_tools(tools: list[BaseTool]) -> dict[str, Any]:
    """Build the minimum HITL policy from attached tool risk metadata."""

    policy = default_deepagents_interrupt_policy()
    for tool in tools:
        policy.update(interrupt_policy_for_tool(tool))
    return policy


def _build_interrupt_on_policy(
    middleware_configs: list[dict[str, Any]] | None,
    tools: list[BaseTool],
    *,
    include_ask_user: bool,
    is_trigger_mode: bool,
) -> dict[str, Any] | None:
    """Build the DeepAgents top-level ``interrupt_on`` policy.

    DeepAgents propagates top-level HITL policy to its built-in subagent
    middleware. Keep the policy out of the explicit middleware list so
    ``ask_user`` and delegated tool calls share the same standard path.
    """

    if is_trigger_mode:
        return None

    interrupt_on: dict[str, Any] = _default_interrupt_on_from_tools(tools)
    for mw_config in middleware_configs or []:
        if mw_config.get("type") != "human_in_the_loop":
            continue
        explicit = mw_config.get("params", {}).get("interrupt_on")
        if isinstance(explicit, dict):
            interrupt_on = merge_interrupt_policies(interrupt_on, explicit)
        break

    policy = dict(interrupt_on or {})
    if include_ask_user:
        policy.setdefault("ask_user", {"allowed_decisions": ["respond"]})
    return policy or None


def _selected_skill_slugs(agent_skills: list[dict[str, Any]] | None) -> list[str]:
    if not agent_skills:
        return []
    slugs: list[str] = []
    for raw in agent_skills:
        slug = raw.get("slug")
        if isinstance(slug, str) and slug:
            slugs.append(slug)
    return slugs


def _system_prompt_with_temporal_context(system_prompt: str) -> str:
    block = build_temporal_context_prompt().strip()
    prompt = system_prompt.strip()
    return f"{prompt}\n\n{block}" if prompt else block


def _parse_uuid(value: str | None) -> _uuid.UUID | None:
    if not value:
        return None
    try:
        return _uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _load_memory_prompt(cfg: AgentConfig) -> str:
    user_uuid = _parse_uuid(cfg.user_id)
    if user_uuid is None:
        return ""
    agent_uuid = _parse_uuid(cfg.agent_id)
    try:
        from app.database import async_session as _async_session_factory
        from app.services import memory_service

        async with _async_session_factory() as db:
            policy = await memory_service.resolve_effective_policy(
                db,
                user_id=user_uuid,
                agent_id=agent_uuid,
            )
            if not policy.read_enabled:
                return ""
            records = await memory_service.list_runtime_memory_records(
                db,
                user_id=user_uuid,
                agent_id=agent_uuid,
                allowed_scopes=policy.allowed_scopes,
            )
            return memory_service.render_memory_prompt(records)
    except Exception:  # noqa: BLE001 — memory is helpful context, not a hard runtime dependency
        logger.warning("memory prompt load failed", exc_info=True)
        return ""


async def _memory_write_policy_for_run(cfg: AgentConfig, *, is_trigger_mode: bool) -> str:
    user_uuid = _parse_uuid(cfg.user_id)
    if user_uuid is None:
        return "off"
    agent_uuid = _parse_uuid(cfg.agent_id)
    try:
        from app.database import async_session as _async_session_factory
        from app.services import memory_service

        async with _async_session_factory() as db:
            policy = await memory_service.resolve_effective_policy(
                db,
                user_id=user_uuid,
                agent_id=agent_uuid,
            )
            return policy.trigger_write_policy if is_trigger_mode else policy.write_policy
    except Exception:  # noqa: BLE001 — memory writes are optional runtime affordances
        logger.warning("memory write policy load failed", exc_info=True)
        return "off"


def _memory_tool_instruction_prompt() -> str:
    return (
        "## Long-term Memory Tool Rules\n"
        "- If the user explicitly asks you to remember, save, or persist a durable "
        "preference or fact, call `propose_memory`, `save_user_memory`, or "
        "`save_agent_memory` instead of only describing what you would do.\n"
        "- Use `propose_memory` when you are unsure whether the memory should be "
        "user-wide or agent-specific; use `save_user_memory` for user-wide "
        "preferences and `save_agent_memory` for this agent's operating notes.\n"
        "- The server enforces the user's memory policy. In ask mode, save tools "
        "create an approval proposal rather than directly storing the memory.\n"
        "- Do not claim a memory was saved unless a memory tool result says "
        "`memory_saved`. If the tool reports `memory_proposed`, tell the user it "
        "is waiting for approval.\n"
        "- Never store API keys, passwords, tokens, credentials, or government ID "
        "numbers. Ordinary test labels or preference IDs are not secrets by "
        "themselves."
    )


async def _prepare_runtime_components(
    cfg: AgentConfig,
    *,
    is_trigger_mode: bool,
    include_ask_user: bool,
    include_agent_memory_file: bool,
    timings: dict[str, int] | None = None,
) -> RuntimeComponents:
    """Build reusable Deep Agents runtime pieces for a parent or child agent."""

    last_mark = time.perf_counter()

    def mark_timing(name: str) -> None:
        nonlocal last_mark
        if timings is None:
            return
        now = time.perf_counter()
        timings[name] = int((now - last_mark) * 1000)
        last_mark = now

    system_prompt = _system_prompt_with_temporal_context(cfg.system_prompt)
    model_candidates = _build_model_candidates(cfg)
    model = model_candidates[0]
    mark_timing("model_ms")

    langchain_tools: list[BaseTool] = []
    mcp_configs: list[dict] = []
    runtime_tool_configs = [
        *cfg.tools_config,
        *build_skill_dependency_tool_configs(
            agent_skills=cfg.agent_skills or [],
            existing_tool_configs=cfg.tools_config,
            user_id=cfg.user_id,
            agent_id=cfg.agent_id,
        ),
    ]

    for tc in runtime_tool_configs:
        if tc.get("mcp_server_url"):
            mcp_configs.append(tc)
            continue
        tool = create_tool_for_runtime(tc)
        if tool is not None:
            langchain_tools.append(tool)

    langchain_tools.extend(await _build_mcp_tools(mcp_configs))
    _append_temporal_tools(langchain_tools)

    memory_write_policy = await _memory_write_policy_for_run(
        cfg,
        is_trigger_mode=is_trigger_mode,
    )
    memory_tools_enabled = bool(cfg.user_id and memory_write_policy != "off")
    if memory_tools_enabled:
        langchain_tools.extend(
            build_memory_tools(
                user_id=cfg.user_id,
                agent_id=cfg.agent_id,
                conversation_id=cfg.thread_id,
                is_trigger_mode=is_trigger_mode,
            )
        )
    mark_timing("tools_ms")

    configured_mw_types = {
        str(c.get("type")) for c in (cfg.middleware_configs or []) if c.get("type")
    }
    filtered_mw = [
        c for c in (cfg.middleware_configs or []) if c.get("type") not in DEEPAGENT_BUILTIN_TYPES
    ]
    resolved_mw = _resolve_middleware_model_params(filtered_mw, cfg.provider_api_keys or {})
    middleware = _build_default_reliability_middleware(
        model_candidates,
        configured_types=configured_mw_types,
    )
    middleware += build_middleware_instances(resolved_mw)
    middleware += get_provider_middleware(cfg.provider)
    mark_timing("middleware_ms")

    backend = FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)

    skills_sources: list[str] | None = None
    if cfg.agent_skills:
        skill_ctx = build_skill_runtime_context(
            cfg,
            data_dir=_DATA_DIR,
            output_root=Path(settings.conversation_output_dir),
        )
        if cfg.user_id:
            from app.database import async_session as _async_session_factory

            async with _async_session_factory() as _runtime_db:
                await resolve_runtime_credentials(skill_ctx, db=_runtime_db, cfg=cfg)
        skills_virtual_prefix = (
            f"/runtime/{cfg.thread_id}/agents/{cfg.agent_runtime_name}/skills/"
            if cfg.agent_runtime_name
            else f"/runtime/{cfg.thread_id}/skills/"
        )
        skills_sources = [skills_virtual_prefix]
        langchain_tools.append(_create_skill_execute_tool(skill_ctx))
        system_prompt += (
            "\n\n## 스킬 사용 규칙\n"
            "스킬을 사용할 때는 반드시 read_file 도구로 SKILL.md를 먼저 읽고 "
            "그 안의 지시를 직접 따르세요. "
            "스크립트 실행이 필요하면 execute_in_skill 도구를 사용하세요. "
            "task 도구의 subagent_type에 스킬 이름을 넣지 마세요. "
            "task 도구를 사용할 때 subagent_type은 task 도구 설명에 표시된 "
            "available subagent types 중 하나여야 합니다.\n"
            "스크립트 실행 후 OUTPUT_FILES에 이미지가 있으면 "
            "![image](/api/conversations/" + cfg.thread_id + "/files/<파일명>) 형식으로 표시하세요."
        )

        from app.skills.prompt import build_skills_prompt

        skills_block = build_skills_prompt(cfg.agent_skills)
        if skills_block:
            skills_block = skills_block.replace("/skills/", skills_virtual_prefix)
            system_prompt += "\n" + skills_block

    memory_sources: list[str] | None = None
    if include_agent_memory_file and cfg.agent_id:
        (_DATA_DIR / "agents" / cfg.agent_id).mkdir(parents=True, exist_ok=True)
        memory_sources = [f"/agents/{cfg.agent_id}/AGENTS.md"]

    if memory_tools_enabled:
        system_prompt += "\n\n" + _memory_tool_instruction_prompt()

    if include_agent_memory_file:
        memory_prompt = await _load_memory_prompt(cfg)
        if memory_prompt:
            system_prompt += "\n\n" + memory_prompt

    permissions = build_filesystem_permissions(
        thread_id=cfg.thread_id,
        agent_id=cfg.agent_id,
        user_id=cfg.user_id,
        selected_skill_slugs=_selected_skill_slugs(cfg.agent_skills),
        agent_runtime_name=cfg.agent_runtime_name,
    )

    if include_ask_user and not is_trigger_mode:
        langchain_tools.append(ask_user_tool)

    interrupt_on = _build_interrupt_on_policy(
        cfg.middleware_configs,
        langchain_tools,
        include_ask_user=any(t.name == "ask_user" for t in langchain_tools),
        is_trigger_mode=is_trigger_mode,
    )
    mark_timing("skills_filesystem_ms")

    return RuntimeComponents(
        model_candidates=model_candidates,
        model=model,
        tools=langchain_tools,
        middleware=middleware,
        system_prompt=system_prompt,
        skills_sources=skills_sources,
        backend=backend,
        memory_sources=memory_sources,
        permissions=permissions,
        interrupt_on=interrupt_on,
    )


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
    prepare_started = time.perf_counter()
    last_mark = prepare_started
    timings: dict[str, int] = {}

    def mark_timing(name: str) -> None:
        nonlocal last_mark
        now = time.perf_counter()
        timings[name] = int((now - last_mark) * 1000)
        last_mark = now

    components = await _prepare_runtime_components(
        cfg,
        is_trigger_mode=is_trigger_mode,
        include_ask_user=not is_trigger_mode,
        include_agent_memory_file=True,
        timings=timings,
    )
    last_mark = time.perf_counter()

    # 5. 에이전트 빌드 — create_deep_agent + checkpointer
    from app.agent_runtime.checkpointer import get_checkpointer

    build_started = time.perf_counter()
    agent = build_agent(
        components.model,
        components.tools,
        components.system_prompt,
        middleware=components.middleware or None,
        interrupt_on=components.interrupt_on,
        checkpointer=get_checkpointer(),
        backend=components.backend,
        skills=components.skills_sources,
        memory=components.memory_sources,
        permissions=components.permissions,
        name=cfg.agent_runtime_name or f"agent_{cfg.thread_id[:8]}",
        subagents=cfg.subagents_config,
    )
    timings["build_agent_ms"] = int((time.perf_counter() - build_started) * 1000)
    last_mark = time.perf_counter()

    lc_messages = convert_to_langchain_messages(messages_history)
    mark_timing("messages_ms")
    config: dict[str, Any] = {"configurable": {"thread_id": cfg.thread_id}}
    recursion_limit = _configured_recursion_limit(cfg)
    if recursion_limit is not None:
        config["recursion_limit"] = recursion_limit
    if cfg.checkpoint_id:
        # LangGraph time-travel: invoking with an explicit checkpoint_id forks
        # a new branch from that point. The new run's checkpoints chain back to
        # this id, and `alist` reveals both branches as siblings of the parent.
        config["configurable"]["checkpoint_id"] = cfg.checkpoint_id

    timings["total_ms"] = int((time.perf_counter() - prepare_started) * 1000)
    timing_payload = " ".join(f"{key}={value}" for key, value in timings.items())
    log_message = (
        "agent_prepare_timing "
        f"agent_id={cfg.agent_id} thread_id={cfg.thread_id} "
        f"tools={len(components.tools)} skills={len(cfg.agent_skills or [])} "
        f"{timing_payload}"
    )
    logger.debug(log_message)
    if timings["total_ms"] >= 250:
        logger.info(log_message)

    return agent, lc_messages, config


def _hook_ctx_for_agent(cfg: AgentConfig) -> HookContext | None:
    """Build a ``HookContext`` for an ``agent_invoke`` call.

    Returns ``None`` when the caller didn't propagate a ``user_id`` (legacy
    tests that build ``AgentConfig`` directly). Hook dispatch is a no-op in
    that case so the runtime stays backward compatible.

    Correlation IDs (``agent_id`` / ``model_id`` / ``llm_credential_id``)
    are best-effort: a malformed UUID drops the field instead of crashing
    the request — these are trace metadata, not access-control gates.
    The user_id check above is the security boundary.
    """

    if not cfg.user_id:
        return None
    try:
        user_uuid = _uuid.UUID(str(cfg.user_id))
    except (TypeError, ValueError):
        return None

    def _opt(value: str | None) -> _uuid.UUID | None:
        if not value:
            return None
        try:
            return _uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    metadata: dict[str, Any] = {
        "provider": cfg.provider,
        "model_name": cfg.model_name,
        "thread_id": cfg.thread_id,
        "identity_mode": cfg.identity_mode,
        "credential_subject_user_id": cfg.credential_subject_user_id,
        "agent_runtime_name": cfg.agent_runtime_name,
    }
    return HookContext(
        request_id=str(_uuid.uuid4()),
        kind="agent_invoke",
        user_id=user_uuid,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        agent_id=_opt(cfg.agent_id),
        model_id=_opt(cfg.model_id),
        credential_id=_opt(cfg.llm_credential_id),
        metadata=metadata,
    )


def _hook_result_from_usage(duration_ms: int, usage_sink: dict[str, Any]) -> HookResult:
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
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    artifact_recorder: Any | None = None,
    moldy_source: str = "chat",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
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

    langfuse_ctx = build_langfuse_run_context(
        cfg,
        run_id=run_id,
        source=moldy_source,
    )
    config = langfuse_ctx.configure_config(config)
    if langfuse_sink is not None and langfuse_ctx.trace is not None:
        langfuse_sink.append(langfuse_ctx.trace)

    ctx = _hook_ctx_for_agent(cfg)
    if ctx is not None:
        if hook_metadata_extra:
            ctx.metadata.update(hook_metadata_extra)
        await hooks.run_pre(ctx)
    started = time.monotonic()
    usage_sink: dict[str, Any] = {}
    stream_errors = error_sink if error_sink is not None else []

    activate = getattr(langfuse_ctx, "activate", None)
    activation = (
        activate(input_payload=actual_input, output_payload=None)
        if callable(activate)
        else nullcontext()
    )
    try:
        with activation:
            async for chunk in stream_agent_response(
                agent,
                actual_input,
                config,
                cost_per_input_token=cfg.cost_per_input_token,
                cost_per_output_token=cfg.cost_per_output_token,
                usage_sink=usage_sink,
                trace_sink=trace_sink,
                msg_id_sink=msg_id_sink,
                error_sink=stream_errors,
                broker=broker,
                persist_callback=persist_callback,
                run_id=run_id,
                subagent_display_names=cfg.subagent_display_names,
                artifact_recorder=artifact_recorder,
            ):
                yield chunk
    except Exception as exc:
        if ctx is not None:
            await hooks.run_failure(ctx, exc)
        raise
    finally:
        langfuse_ctx.flush()
    if stream_errors:
        if ctx is not None:
            await hooks.run_failure(ctx, stream_errors[0].error)
        return
    if ctx is not None:
        await hooks.run_post(
            ctx,
            _hook_result_from_usage(int((time.monotonic() - started) * 1000), usage_sink),
        )


# Sentinel that tells ``_run_agent_stream`` to feed its prepped lc_messages
# straight into ``stream_agent_response`` (execute path). Resume path passes a
# concrete ``Command(resume=...)`` instead.
_USE_PREPPED_LC_MESSAGES: Any = object()


async def execute_agent_stream(
    cfg: AgentConfig,
    messages_history: list[dict[str, str]] | dict[str, Any],
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    artifact_recorder: Any | None = None,
    moldy_source: str = "chat",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
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

    # dict input — fork-edit가 Overwrite({"messages": [...]}) 같은 형태로
    # state 채널을 직접 덮어쓸 때 사용. 이 경우 _prepare_agent는 lc_messages
    # 변환을 건너뛰고(빈 리스트), stream_input으로 dict를 그대로 흘려보낸다.
    if isinstance(messages_history, dict):
        async for chunk in _run_agent_stream(
            cfg,
            messages_history=[],
            stream_input=messages_history,
            trace_sink=trace_sink,
            msg_id_sink=msg_id_sink,
            error_sink=error_sink,
            broker=broker,
            persist_callback=persist_callback,
            run_id=run_id,
            artifact_recorder=artifact_recorder,
            moldy_source=moldy_source,
            langfuse_sink=langfuse_sink,
        ):
            yield chunk
        return

    async for chunk in _run_agent_stream(
        cfg,
        messages_history=messages_history,
        stream_input=_USE_PREPPED_LC_MESSAGES,
        trace_sink=trace_sink,
        msg_id_sink=msg_id_sink,
        error_sink=error_sink,
        broker=broker,
        persist_callback=persist_callback,
        run_id=run_id,
        artifact_recorder=artifact_recorder,
        moldy_source=moldy_source,
        langfuse_sink=langfuse_sink,
    ):
        yield chunk


async def resume_agent_stream(
    cfg: AgentConfig,
    resume_value: Any,
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: Any | None = None,
    persist_callback: Any | None = None,
    run_id: str | None = None,
    artifact_recorder: Any | None = None,
    moldy_source: str = "resume",
    langfuse_sink: list[LangfuseTraceRecord] | None = None,
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
        error_sink=error_sink,
        broker=broker,
        persist_callback=persist_callback,
        run_id=run_id,
        artifact_recorder=artifact_recorder,
        moldy_source=moldy_source,
        langfuse_sink=langfuse_sink,
    ):
        yield chunk


async def execute_agent_invoke(
    cfg: AgentConfig,
    messages_history: list[dict[str, str]],
    *,
    run_id: str | None = None,
    moldy_source: str = "trigger",
) -> str:
    """비스트리밍 실행 (트리거용). 최종 응답 텍스트만 반환."""
    agent, lc_messages, config = await _prepare_agent(
        cfg,
        messages_history=messages_history,
        is_trigger_mode=True,
    )

    effective_run_id = run_id or str(_uuid.uuid4())
    langfuse_ctx = build_langfuse_run_context(
        cfg,
        run_id=effective_run_id,
        source=moldy_source,
    )
    config = langfuse_ctx.configure_config(config)

    ctx = _hook_ctx_for_agent(cfg)
    if ctx is not None:
        await hooks.run_pre(ctx)
    started = time.monotonic()

    activate = getattr(langfuse_ctx, "activate", None)
    activation = (
        activate(input_payload={"messages": lc_messages}, output_payload=None)
        if callable(activate)
        else nullcontext()
    )
    try:
        with activation:
            result = await agent.ainvoke({"messages": lc_messages}, config=config)
    except Exception as exc:
        if ctx is not None:
            await hooks.run_failure(ctx, exc)
        raise
    finally:
        langfuse_ctx.flush()

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
