from __future__ import annotations

import logging
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
from app.agent_runtime.mcp_tool_loader import _build_mcp_tools
from app.agent_runtime.message_utils import convert_to_langchain_messages
from app.agent_runtime.middleware_registry import (
    DEEPAGENT_BUILTIN_TYPES,
    build_middleware_instances,
    get_provider_middleware,
)
from app.agent_runtime.model_factory import create_chat_model
from app.agent_runtime.runtime_config import _DATA_DIR, AgentConfig, RuntimeComponents
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.agent_runtime.skill_tool_dependencies import build_skill_dependency_tool_configs
from app.agent_runtime.temporal import build_temporal_context_prompt
from app.agent_runtime.tool_factory import create_builtin_tool, create_tool_for_runtime
from app.agent_runtime.tools.ask_user import ask_user as ask_user_tool
from app.agent_runtime.tools.memory import build_memory_tools
from app.config import settings
from app.exceptions import AppError
from app.marketplace.skill_runtime import build_skill_runtime_context, resolve_runtime_credentials
from app.tools.risk import (
    default_deepagents_interrupt_policy,
    interrupt_policy_for_tool,
    merge_interrupt_policies,
)

logger = logging.getLogger(__name__)

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
        subagents=cast(Any, subagents),
    )


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


def _interactive_tool_instruction_prompt() -> str:
    return (
        "## Interactive Tool Rules\n"
        "- If the user explicitly asks you to ask the user, use ask_user, "
        "let them choose, or pick from options, call the `ask_user` tool. "
        "Do not answer with plain text that only describes asking.\n"
        "- For a single-choice option request, call `ask_user` with "
        '`mode="option_list"`, a concise title, the requested options, '
        "`minSelections=1`, and `maxSelections=1`.\n"
        "- If the user explicitly asks to use an available tool or MCP tool, "
        "call the matching tool instead of simulating the tool result in text.\n"
        "- If a tool requires HITL approval, wait for the approval result before "
        "claiming that the tool ran or that the requested side effect happened."
    )


def _artifact_file_instruction_prompt(thread_id: str) -> str:
    return (
        "## Generated File Rules\n"
        f"- When the user asks you to create, save, or output a file, call `write_file` "
        f"with an absolute path under `/conversations/{thread_id}/`.\n"
        f"- Example: `/conversations/{thread_id}/report.md` or "
        f"`/conversations/{thread_id}/charts/summary.csv`.\n"
        "- Do not use `/tmp`, `/runtime`, `/skills`, or `/agents` for user-visible "
        "generated files; those paths are not shown as chat artifacts and may be rejected.\n"
        "- After a file tool succeeds, briefly tell the user the file is ready. "
        "Do not claim the file was saved if the tool result reports an error."
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
    system_prompt += "\n\n" + _artifact_file_instruction_prompt(cfg.thread_id)
    if include_ask_user and not is_trigger_mode:
        system_prompt += "\n\n" + _interactive_tool_instruction_prompt()
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
    memory_tools_enabled = cfg.user_id is not None and memory_write_policy != "off"
    if memory_tools_enabled:
        memory_user_id = cfg.user_id
        assert memory_user_id is not None
        langchain_tools.extend(
            build_memory_tools(
                user_id=memory_user_id,
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
