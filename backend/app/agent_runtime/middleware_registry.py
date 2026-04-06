"""Middleware registry for AI agent middleware configuration.

Defines 22 middleware types that can be attached to agents.
Actual langchain.agents.middleware imports are deferred — if the package
is not yet installed, build_middleware_instances() returns an empty list.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry: 22 middleware definitions
# ---------------------------------------------------------------------------

MIDDLEWARE_REGISTRY: dict[str, dict[str, Any]] = {
    # ---- context (3) ----
    "summarization": {
        "name": "SummarizationMiddleware",
        "display_name": "대화 자동 요약",
        "description": "토큰 한계에 도달하면 대화 히스토리를 자동 요약합니다",
        "category": "context",
        "config_schema": {
            "trigger": {
                "type": "tuple",
                "default": ["tokens", 4000],
                "description": "요약 트리거 조건 (tokens/messages, 값)",
            },
            "keep": {
                "type": "tuple",
                "default": ["messages", 20],
                "description": "요약 시 유지할 메시지 수",
            },
            "model": {
                "type": "string",
                "default": "openai:gpt-4.1-mini",
                "description": "요약에 사용할 모델",
            },
        },
        "provider_specific": None,
    },
    "context_editing": {
        "name": "ContextEditingMiddleware",
        "display_name": "컨텍스트 정리",
        "description": "오래된 도구 결과를 정리하여 컨텍스트 윈도우를 관리합니다",
        "category": "context",
        "config_schema": {},
        "provider_specific": None,
    },
    "filesystem": {
        "name": "FilesystemMiddleware",
        "display_name": "파일시스템 접근",
        "description": "에이전트에 파일 읽기/쓰기/편집 도구를 제공합니다",
        "category": "context",
        "config_schema": {},
        "provider_specific": None,
    },
    # ---- planning (2) ----
    "todo_list": {
        "name": "TodoListMiddleware",
        "display_name": "작업 계획 및 추적",
        "description": "에이전트가 복잡한 작업을 계획하고 진행 상황을 추적합니다",
        "category": "planning",
        "config_schema": {},
        "provider_specific": None,
    },
    "subagent": {
        "name": "SubAgentMiddleware",
        "display_name": "서브에이전트 위임",
        "description": "전문 서브에이전트에 작업을 위임하여 복잡한 작업을 분업합니다",
        "category": "planning",
        "config_schema": {},
        "provider_specific": None,
    },
    # ---- safety (3) ----
    "human_in_the_loop": {
        "name": "HumanInTheLoopMiddleware",
        "display_name": "사용자 승인 게이트",
        "description": "위험한 도구 실행 전 사용자 승인을 요청합니다",
        "category": "safety",
        "config_schema": {
            "interrupt_on": {
                "type": "object",
                "default": {},
                "description": "승인이 필요한 도구 목록 (도구명: true)",
            },
        },
        "provider_specific": None,
    },
    "pii": {
        "name": "PIIMiddleware",
        "display_name": "PII 보호",
        "description": "개인식별정보(이메일, 신용카드, IP 등)를 감지하고 마스킹합니다",
        "category": "safety",
        "config_schema": {},
        "provider_specific": None,
    },
    "shell_tool": {
        "name": "ShellToolMiddleware",
        "display_name": "쉘 명령어 실행",
        "description": "에이전트에 영속 쉘 세션을 제공합니다",
        "category": "safety",
        "config_schema": {},
        "provider_specific": None,
    },
    # ---- reliability (7) ----
    "llm_tool_selector": {
        "name": "LLMToolSelectorMiddleware",
        "display_name": "도구 자동 선택",
        "description": "LLM이 관련 도구만 선택하여 정확도를 향상시킵니다",
        "category": "reliability",
        "config_schema": {},
        "provider_specific": None,
    },
    "model_call_limit": {
        "name": "ModelCallLimitMiddleware",
        "display_name": "모델 호출 제한",
        "description": "LLM 호출 횟수를 제한하여 무한 루프를 방지합니다",
        "category": "reliability",
        "config_schema": {
            "thread_limit": {
                "type": "integer",
                "default": 10,
                "description": "스레드당 최대 모델 호출 횟수",
            },
            "run_limit": {
                "type": "integer",
                "default": 5,
                "description": "실행당 최대 모델 호출 횟수",
            },
            "exit_behavior": {
                "type": "string",
                "default": "end",
                "description": "제한 초과 시 동작 (end/error)",
            },
        },
        "provider_specific": None,
    },
    "tool_retry": {
        "name": "ToolRetryMiddleware",
        "display_name": "도구 재시도",
        "description": "도구 실패 시 지수 백오프로 자동 재시도합니다",
        "category": "reliability",
        "config_schema": {
            "max_retries": {
                "type": "integer",
                "default": 3,
                "description": "최대 재시도 횟수",
            },
        },
        "provider_specific": None,
    },
    "tool_call_limit": {
        "name": "ToolCallLimitMiddleware",
        "display_name": "도구 호출 제한",
        "description": "도구 호출 횟수를 제한합니다",
        "category": "reliability",
        "config_schema": {
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "최대 도구 호출 횟수",
            },
        },
        "provider_specific": None,
    },
    "model_fallback": {
        "name": "ModelFallbackMiddleware",
        "display_name": "모델 자동 전환",
        "description": "주 모델 실패 시 대체 모델로 자동 전환합니다",
        "category": "reliability",
        "config_schema": {
            "fallback_model": {
                "type": "string",
                "default": "openai:gpt-4.1-mini",
                "description": "대체 모델 식별자",
            },
        },
        "provider_specific": None,
    },
    "model_retry": {
        "name": "ModelRetryMiddleware",
        "display_name": "모델 호출 재시도",
        "description": "LLM 호출 실패 시 지수 백오프로 재시도합니다",
        "category": "reliability",
        "config_schema": {
            "max_retries": {
                "type": "integer",
                "default": 3,
                "description": "최대 재시도 횟수",
            },
        },
        "provider_specific": None,
    },
    "file_search": {
        "name": "FilesystemFileSearchMiddleware",
        "display_name": "파일 검색",
        "description": "대용량 문서에서 glob/grep 검색 기능을 제공합니다",
        "category": "reliability",
        "config_schema": {},
        "provider_specific": None,
    },
    "llm_tool_emulator": {
        "name": "LLMToolEmulator",
        "display_name": "도구 에뮬레이터",
        "description": "LLM으로 도구 실행을 에뮬레이션합니다 (테스트용)",
        "category": "reliability",
        "config_schema": {},
        "provider_specific": None,
    },
    # ---- provider-specific (6) ----
    "anthropic_prompt_caching": {
        "name": "AnthropicPromptCachingMiddleware",
        "display_name": "Anthropic 프롬프트 캐싱",
        "description": "시스템 프롬프트를 캐시하여 최대 75% 비용을 절감합니다",
        "category": "provider",
        "config_schema": {},
        "provider_specific": "anthropic",
    },
    "anthropic_memory": {
        "name": "AnthropicMemoryMiddleware",
        "display_name": "Anthropic 지속 메모리",
        "description": "Anthropic의 지속 메모리 기능을 활용합니다",
        "category": "provider",
        "config_schema": {},
        "provider_specific": "anthropic",
    },
    "anthropic_bash_tool": {
        "name": "AnthropicBashToolMiddleware",
        "display_name": "Anthropic Bash 도구",
        "description": "Claude 모델에서 Bash 명령어 실행을 지원합니다",
        "category": "provider",
        "config_schema": {},
        "provider_specific": "anthropic",
    },
    "anthropic_file_search": {
        "name": "AnthropicFileSearchMiddleware",
        "display_name": "Anthropic 파일 검색",
        "description": "Claude 모델에서 대용량 문서 검색을 지원합니다",
        "category": "provider",
        "config_schema": {},
        "provider_specific": "anthropic",
    },
    "anthropic_text_editor": {
        "name": "AnthropicTextEditorMiddleware",
        "display_name": "Anthropic 텍스트 편집기",
        "description": "Claude 모델에서 텍스트 편집 도구를 지원합니다",
        "category": "provider",
        "config_schema": {},
        "provider_specific": "anthropic",
    },
    "openai_moderation": {
        "name": "OpenAIModerationMiddleware",
        "display_name": "OpenAI 콘텐츠 모더레이션",
        "description": "GPT 모델에서 콘텐츠 안전성을 검사합니다",
        "category": "provider",
        "config_schema": {},
        "provider_specific": "openai",
    },
}

# Map middleware type key → class name for dynamic import
_CLASS_MAP: dict[str, str] = {k: v["name"] for k, v in MIDDLEWARE_REGISTRY.items()}


def _patched_llm_tool_selector_class() -> type | None:
    """Return a patched LLMToolSelectorMiddleware that normalizes response format.

    ADR-004: deepagents가 {"const": "name"} 정규화를 내부 처리하지 않음.
    GPT-4o + llm_tool_selector 조합 시 패치 없으면 깨짐. 유지 필요.

    GPT-4o sometimes returns {"const": "tool_name"} objects instead of plain
    "tool_name" strings when using structured output with const schemas.
    This subclass normalizes both formats before processing.
    """
    try:
        from langchain.agents.middleware import LLMToolSelectorMiddleware
    except (ImportError, ModuleNotFoundError):
        return None

    class PatchedLLMToolSelectorMiddleware(LLMToolSelectorMiddleware):
        def _process_selection_response(self, response, available_tools, valid_tool_names, request):
            # Normalize {"const": "name"} objects to plain "name" strings
            if "tools" in response:
                normalized = []
                for item in response["tools"]:
                    if isinstance(item, dict) and "const" in item:
                        normalized.append(item["const"])
                    elif isinstance(item, str):
                        normalized.append(item)
                    else:
                        normalized.append(str(item))
                response = {**response, "tools": normalized}
            return super()._process_selection_response(
                response, available_tools, valid_tool_names, request
            )

    return PatchedLLMToolSelectorMiddleware


def _resolve_middleware_class(middleware_type: str) -> type | None:
    """Attempt to import a middleware class from langchain.agents.middleware.

    Returns None if the package is not installed or the class is unavailable.
    """
    # Use patched version for llm_tool_selector
    if middleware_type == "llm_tool_selector":
        return _patched_llm_tool_selector_class()

    class_name = _CLASS_MAP.get(middleware_type)
    if not class_name:
        return None
    try:
        import importlib

        module = importlib.import_module("langchain.agents.middleware")
        return getattr(module, class_name, None)
    except (ImportError, ModuleNotFoundError):
        return None


def _coerce_tuple_params(params: dict[str, Any], config_schema: dict[str, Any]) -> dict[str, Any]:
    """Convert list values back to tuples for parameters declared as tuple type."""
    result = dict(params)
    for key, schema in config_schema.items():
        if schema.get("type") == "tuple" and key in result and isinstance(result[key], list):
            result[key] = tuple(result[key])
    return result


def build_middleware_instances(middleware_configs: list[dict[str, Any]]) -> list:
    """Build middleware instances from a list of config dicts.

    Each dict must have:
      - "type": middleware registry key (e.g. "summarization")
      - "params": optional dict of constructor kwargs

    Returns a list of middleware instances. If langchain.agents.middleware
    is not importable, returns an empty list.
    """
    instances: list = []
    for config in middleware_configs:
        middleware_type = config.get("type", "")
        params = config.get("params", {})

        registry_entry = MIDDLEWARE_REGISTRY.get(middleware_type)
        if not registry_entry:
            logger.warning("Unknown middleware type: %s", middleware_type)
            continue

        cls = _resolve_middleware_class(middleware_type)
        if cls is None:
            logger.debug(
                "Middleware class for '%s' not available (langchain.agents.middleware "
                "not installed or class missing). Skipping.",
                middleware_type,
            )
            continue

        coerced = _coerce_tuple_params(params, registry_entry.get("config_schema", {}))
        try:
            instances.append(cls(**coerced))
        except Exception:
            logger.exception("Failed to instantiate middleware '%s'", middleware_type)
    return instances


def get_provider_middleware(provider: str) -> list:
    """Return auto-applied middleware instances for a given model provider.

    E.g. Anthropic models automatically get prompt caching middleware.
    Returns an empty list if the middleware classes are not importable.
    """
    provider_map: dict[str, list[str]] = {
        "anthropic": ["anthropic_prompt_caching"],
        "openai": ["openai_moderation"],
    }
    types = provider_map.get(provider, [])
    if not types:
        return []
    return build_middleware_instances([{"type": t, "params": {}} for t in types])


def get_middleware_registry() -> list[dict[str, Any]]:
    """Return the full middleware catalog for the frontend.

    Each entry includes type key plus all metadata (name, display_name,
    description, category, config_schema, provider_specific).
    """
    return [{"type": key, **entry} for key, entry in MIDDLEWARE_REGISTRY.items()]
