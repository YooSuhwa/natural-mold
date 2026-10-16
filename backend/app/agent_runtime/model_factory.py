"""LLM model factory — wrap provider SDKs in a uniform LangChain interface.

Greenfield M5: API keys live exclusively in :class:`Credential` rows now, so
``PROVIDER_API_KEY_MAP`` and the legacy ``llm_provider`` join are gone. The
caller (:mod:`app.services.chat_service` via the conversations router and the
trigger executor) decrypts ``Agent.llm_credential`` and passes the resolved
``api_key`` here. Env-var fallback is retained for the small set of internal
sub-agents (Builder/Assistant) that don't have a credential of their own.

M10 adds :func:`create_chat_model_with_fallback`, an opt-in wrapper that
walks the ``Agent.model_fallback_list`` chain on transient/auth errors. The
fallback walk pattern is borrowed from prior art — see ``NOTICES.md`` for
the LiteLLM router fallback reference. Identifiers and audit log shape are
Moldy-native; the wrapper does not import or copy any external code.
"""

from __future__ import annotations

import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.agent_runtime.model_client_cache import (
    _MODEL_CACHE,
    _MODEL_CACHE_MAXSIZE,
    _api_key_fingerprint,
    _await_close_result,
    _close_cached_model,
    _close_maybe_async,
    _hc_cert,
    _jsonable,
    _model_cache_key,
    _ssl_ctx,
    clear_model_cache,
)
from app.agent_runtime.model_fallback_factory import (
    _FALLBACK_RECOVERABLE_STATUS,
    _FALLBACK_RECOVERABLE_TYPES,
    _audit_fallback_attempt,
    _is_fallback_recoverable,
    _resolve_model_for_fallback,
    create_chat_model_with_fallback,
)
from app.agent_runtime.model_provider_builders import (
    _apply_anthropic_quirks,
    _apply_context_window_profile,
    _apply_gpt5_quirks,
    _apply_openai_compatible_base_url,
    _apply_openai_ssl_clients,
    create_chat_model,
    create_chat_model_for_test,
)
from app.agent_runtime.model_provider_registry import (
    _ENV_DEFAULTS,
    _ENV_FALLBACK,
    _GPT5_DEFAULT_COMPLETION_TOKENS,
    _GPT5_FAMILY_PREFIXES,
    _OPENAI_FAMILY_BASE_URLS,
    _TEST_COMPLETION_TOKEN_CAP,
    PROVIDER_API_KEY_MAP,
    PROVIDER_MAP,
    TEST_COMPLETION_TOKEN_CAP,
    env_provider_keys,
    is_gpt5_family,
    sync_env_fallback_from_credentials,
)
from app.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "PROVIDER_API_KEY_MAP",
    "PROVIDER_MAP",
    "TEST_COMPLETION_TOKEN_CAP",
    "create_chat_model",
    "create_chat_model_for_test",
    "create_chat_model_with_fallback",
    "clear_model_cache",
    "env_provider_keys",
    "is_gpt5_family",
    "sync_env_fallback_from_credentials",
]
# Legacy import/patch targets kept on the facade (tests patch ``ChatOpenAI``
# and read the env-fallback dicts; callers import ``_is_fallback_recoverable``).
__all__ += ["BaseChatModel", "ChatAnthropic", "ChatGoogleGenerativeAI", "ChatOpenAI", "settings"]
__all__ += ["_ENV_DEFAULTS", "_ENV_FALLBACK", "_FALLBACK_RECOVERABLE_STATUS"]
__all__ += ["_FALLBACK_RECOVERABLE_TYPES", "_GPT5_DEFAULT_COMPLETION_TOKENS"]
__all__ += ["_GPT5_FAMILY_PREFIXES", "_MODEL_CACHE", "_MODEL_CACHE_MAXSIZE"]
__all__ += ["_OPENAI_FAMILY_BASE_URLS", "_TEST_COMPLETION_TOKEN_CAP", "_api_key_fingerprint"]
__all__ += ["_apply_anthropic_quirks", "_apply_context_window_profile", "_apply_gpt5_quirks"]
__all__ += ["_apply_openai_compatible_base_url", "_apply_openai_ssl_clients"]
__all__ += ["_audit_fallback_attempt", "_await_close_result", "_close_cached_model"]
__all__ += ["_close_maybe_async", "_hc_cert", "_is_fallback_recoverable", "_jsonable"]
__all__ += ["_model_cache_key", "_resolve_model_for_fallback", "_ssl_ctx"]
