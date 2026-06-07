from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


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
