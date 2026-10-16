"""Provider registry and env-derived API key fallback for chat models."""

from __future__ import annotations

import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_client_cache import clear_model_cache
from app.config import settings

logger = logging.getLogger("app.agent_runtime.model_factory")

PROVIDER_MAP: dict[str, type[BaseChatModel]] = {
    "openai": ChatOpenAI,
    "anthropic": ChatAnthropic,
    "google": ChatGoogleGenerativeAI,
    "custom": ChatOpenAI,
    "openrouter": ChatOpenAI,
    "openai_compatible": ChatOpenAI,
}

if settings.e2e_scripted_model_enabled:
    if settings.app_env == "production":
        raise RuntimeError("E2E scripted model cannot run in production")
    from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel

    PROVIDER_MAP["e2e_scripted"] = E2EScriptedChatModel


# Internal callers (Builder/Assistant sub-agents) don't have a Credential row;
# they fall back to env-derived settings. End-user agents get their key from
# ``Agent.llm_credential`` via the chat runtime.
_ENV_FALLBACK: dict[str, str] = {
    "openai": settings.openai_api_key,
    "anthropic": settings.anthropic_api_key,
    "google": settings.google_api_key,
    "openrouter": settings.openrouter_api_key,
}

# Backwards-compatible alias used by Assistant/Builder sub-agent helpers.
PROVIDER_API_KEY_MAP = _ENV_FALLBACK


# Snapshot of the .env-derived values captured at module import. Used by the
# credentials sync (ADR-013) to re-seed ``_ENV_FALLBACK`` on every refresh so
# DELETE-of-credential is reflected without having to track removed keys.
# ``settings`` may be mutated in tests; we intentionally take the value once.
_ENV_DEFAULTS: dict[str, str] = dict(_ENV_FALLBACK)


async def sync_env_fallback_from_credentials(db: AsyncSession) -> None:
    """Refresh ``_ENV_FALLBACK`` from the credentials table (ADR-013).

    Priority (per provider): ``.env`` > system credential > user credential.
    The dict is mutated in-place so the ``PROVIDER_API_KEY_MAP`` alias used
    by builder/assistant helpers stays valid (no object replacement).

    Idempotent: every call resets the dict to the import-time ``.env`` snapshot
    before layering credentials on top, so DELETE/PATCH propagates cleanly.
    """

    from app.credentials import service as credential_service

    try:
        cred_keys = await credential_service.get_provider_keys(db)
    except Exception:  # noqa: BLE001 — never let sync crash a request handler
        logger.exception("sync_env_fallback_from_credentials: read failed")
        return

    # Re-seed from the .env snapshot first; .env wins by definition.
    for env_key, default in _ENV_DEFAULTS.items():
        _ENV_FALLBACK[env_key] = default

    # Layer credentials on top *only* where .env is empty — backward compat.
    for env_key, cred_key in cred_keys.items():
        if not _ENV_FALLBACK.get(env_key) and cred_key:
            _ENV_FALLBACK[env_key] = cred_key
    clear_model_cache()


def env_provider_keys() -> dict[str, str | None]:
    """Return the env-var fallback map. Used by ``provider_api_keys`` paths."""

    return {provider: key or None for provider, key in _ENV_FALLBACK.items()}


# OpenAI's reasoning families (o1/o3/o4) and the GPT-5 family ship with the
# Chat Completions API quirk that ``max_tokens`` is rejected — they require
# the new ``max_completion_tokens`` field instead. The OpenAI Python SDK
# raises ``BadRequestError(unsupported_parameter)`` and LangChain's wrapper
# then re-emits a generic "Connection error.", which is opaque for the user.
# Detecting these prefixes lets us pick the right cap up front.
_GPT5_FAMILY_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")

# Default ``max_completion_tokens`` for chat runtime when the caller did not
# pass an explicit cap. Reasoning models burn output tokens on hidden chains
# of thought; if the cap is too tight the visible content stays empty even
# though the API succeeds (200 OK + ``output_token_details.reasoning ≈ cap``).
# Picking a generous default avoids that silent regression for routine prompts.
_GPT5_DEFAULT_COMPLETION_TOKENS = 4096


def is_gpt5_family(provider: str, model_name: str) -> bool:
    """OpenAI GPT-5 / o-series 가족 — ``max_completion_tokens`` 강제, temperature lock.

    Public so other modules (e.g. ``app.services.model_test``) share the
    single source of truth (ADR-014). raw curl preview 와 런타임 wire 가
    drift 하지 않도록 한 곳에서만 판정.
    """

    if provider != "openai":
        return False
    name = (model_name or "").lower()
    return any(name.startswith(p) for p in _GPT5_FAMILY_PREFIXES)


TEST_COMPLETION_TOKEN_CAP = 200
# Backwards-compat alias (이전 commit 에서 underscore prefix 로 export 되었음).
_TEST_COMPLETION_TOKEN_CAP = TEST_COMPLETION_TOKEN_CAP


# Default base URL per OpenAI-compatible provider. Used only when the caller
# (credential payload, model row, or preview body) didn't supply one.
_OPENAI_FAMILY_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    # 사내 표준 게이트웨이. ``model.base_url`` 이 명시돼 있으면 그것을 우선
    # 사용하고, 비어 있을 때만 이 default 가 적용된다. 다른 게이트웨이를
    # 등록할 때는 모델 행의 ``base_url`` 컬럼을 채울 것.
    "openai_compatible": "https://llm-gw.hancom.com/v1",
}
