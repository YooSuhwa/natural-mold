from __future__ import annotations

from dataclasses import dataclass, field
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
    # Single source of truth for the model context limit (``models.context_window``).
    # Forwarded into the LangChain ``model.profile`` so deepagents' auto
    # SummarizationMiddleware triggers at ``0.85 × context_window`` — the same
    # number the chat context gauge reads. ``None`` keeps deepagents' own
    # profile/170k fallback (backward compatible for internal sub-agents).
    context_window: int | None = None
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
    # W2-3 memory recall visibility — briefs of the long-term memory records
    # injected into this run's system prompt. Populated by the component
    # builder (``_load_memory_context``) during prepare, then shipped once per
    # run as the ``moldy.memory_recalled`` stream-head event (same contract as
    # ``subagent_display_names``). ``None``/empty → no event.
    recalled_memories: list[dict[str, Any]] | None = None
    # 스킬 스튜디오 phase 1 (AD-1/AD-2/AD-5) — ``standard`` 이외 값이면
    # ``_prepare_runtime_components`` 가 전용 분기(빌더 프롬프트/도구/드래프트
    # 마운트)를 탄다. ``resolve_agent_context`` 가 Agent.runtime_profile 에서
    # 판독해 채운다.
    runtime_profile: str = "standard"
    # 빌더 세션 컨텍스트 — conversation_id 역참조로 해석된 세션 식별자와
    # ADR-018 상대 워크스페이스 경로. 도구 클로저/권한 마운트가 사용한다.
    skill_builder_session_id: str | None = None
    draft_workspace_path: str | None = None
    # AD-5 ``moldy.skill_draft`` stream-head 페이로드 (recalled_memories 계약).
    # 파일 내용은 싣지 않는다 — 요약(경로/크기/변경 수)만 (§6-7).
    skill_draft_brief: dict[str, Any] | None = None
    # AD-4 스코프드 동의 — 이 세션에서 승인 카드 없이 실행이 허용된 도구명.
    # ``resolve_agent_context`` 가 session.tool_consents 를 읽어 **현재 드래프트의
    # requires_network 상태로 재검증한 뒤** 채운다. prepare 분기가 interrupt
    # 정책에서 제외한다. finalize_skill 은 절대 포함되지 않는다.
    skill_builder_consented_tools: list[str] | None = None
    # AD-4 — 승인 카드에 "이 세션에서 계속 허용" 옵션을 노출할 도구명.
    # eligible − consented − (requires_network 드래프트면 전부 제외).
    # 러너가 인터럽트 wire의 review_configs에 ``session_consent_eligible``
    # 플래그로 주석한다 (langchain ReviewConfig는 여분 키를 보존하지 않으므로
    # 우리 wire 계층에서 주입).
    skill_builder_consent_offer_tools: list[str] | None = None
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
    # ADR-021 — plaintext secret values injected into this run (LLM api_key,
    # tool/MCP credentials, transport headers). Gathered eagerly by
    # ``conversation_stream_service`` and seeded into the run-scoped redaction
    # ContextVar by ``_run_agent_stream``; skill credentials union in lazily.
    # ``default_factory=set`` keeps it per-instance mutable (never shared) and
    # DB-free unit tests get an empty set with unchanged behaviour.
    secret_values: set[str] = field(default_factory=set)

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
