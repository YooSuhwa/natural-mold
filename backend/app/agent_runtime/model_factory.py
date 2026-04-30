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
import os
import ssl
import uuid
from typing import TYPE_CHECKING, Any

import certifi
import httpx
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

if TYPE_CHECKING:
    from app.models.agent import Agent

logger = logging.getLogger(__name__)

PROVIDER_MAP: dict[str, type[BaseChatModel]] = {
    "openai": ChatOpenAI,
    "anthropic": ChatAnthropic,
    "google": ChatGoogleGenerativeAI,
    "custom": ChatOpenAI,
    "openrouter": ChatOpenAI,
    "openai_compatible": ChatOpenAI,
}


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


# SSL 컨텍스트.
#
# 일부 macOS / 사내 VPN 환경에서 OpenAI 인증서 체인이 strict 검증
# (``Missing Authority Key Identifier``)에 걸린다. ``truststore``로
# OS 네이티브 trust store(macOS Keychain / Windows CryptoAPI / Linux
# /etc/ssl)를 사용하면 시스템이 인정한 모든 root CA를 그대로 활용해
# CRL/AKI 같은 deep-validation 이슈를 우회할 수 있다.
#
# ``HC_SSL.pem`` (사내 프록시 인증서) 가 존재하면 추가 trust로 결합한다.
_hc_cert = os.path.expanduser("~/.ssl/HC_SSL.pem")
try:
    import truststore

    _ssl_ctx: ssl.SSLContext = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:  # pragma: no cover — runtime dep, but defensive
    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
if os.path.exists(_hc_cert):
    _ssl_ctx.load_verify_locations(_hc_cert)


def create_chat_model(
    provider: str,
    model_name: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **extra: object,
) -> BaseChatModel:
    """Build a LangChain chat model for ``provider``.

    ``api_key`` is the resolved (decrypted) key from the caller. When ``None``
    the env-var fallback is consulted for ``openai``/``anthropic``/``google``/
    ``openrouter`` only — other providers must receive an explicit key.
    """

    cls = PROVIDER_MAP.get(provider, ChatOpenAI)

    resolved_key = api_key or _ENV_FALLBACK.get(provider) or None
    kwargs: dict[str, Any] = {"model": model_name}
    if resolved_key:
        kwargs["api_key"] = resolved_key
    if base_url:
        kwargs["base_url"] = base_url

    for param in ("temperature", "top_p", "max_tokens"):
        if param in extra and extra[param] is not None:
            if param == "top_p" and extra[param] == 1.0:
                continue
            kwargs[param] = extra[param]

    # Anthropic rejects temperature + top_p simultaneously.
    if provider == "anthropic" and "temperature" in kwargs and "top_p" in kwargs:
        kwargs.pop("top_p")

    kwargs["stream_usage"] = True

    if cls in (ChatOpenAI,):
        kwargs["http_async_client"] = httpx.AsyncClient(verify=_ssl_ctx)
        kwargs["http_client"] = httpx.Client(verify=_ssl_ctx)

    return cls(**kwargs)


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


def _completion_token_cap_kw(provider: str, model_name: str) -> dict[str, Any]:
    """Return the right kwarg shape for ChatOpenAI's token cap.

    OpenAI GPT-5 / reasoning models reject ``max_tokens`` and require
    ``max_completion_tokens``. LangChain's ``ChatOpenAI`` does not yet
    surface that as a top-level constructor argument, so we forward it
    through ``model_kwargs``. Everything else keeps the legacy
    ``max_tokens=10`` shortcut, which LangChain wires straight to OpenAI.
    """

    name = (model_name or "").lower()
    if provider == "openai" and any(name.startswith(p) for p in _GPT5_FAMILY_PREFIXES):
        # GPT-5 family also rejects non-default temperature; drop it and let
        # the API use its locked default (1.0) so the request validates.
        return {"model_kwargs": {"max_completion_tokens": 10}, "_drop_temperature": True}
    return {"max_tokens": 10}


def create_chat_model_for_test(
    provider: str,
    model_name: str,
    *,
    api_key: str | None,
    base_url: str | None = None,
) -> BaseChatModel:
    """Build a deterministic, low-cost LangChain chat model for the test surface.

    Locked-in defaults (token cap = 10, ``temperature=0``) keep test
    invocations cheap and reproducible no matter what model row exists in the
    catalog. The caller is expected to wrap the resulting ``ainvoke`` in an
    ``asyncio.wait_for(...)`` to enforce the timeout — this factory does not
    schedule timers itself.
    """

    cls = PROVIDER_MAP.get(provider, ChatOpenAI)
    cap_kwargs = _completion_token_cap_kw(provider, model_name)
    drop_temperature = cap_kwargs.pop("_drop_temperature", False)

    kwargs: dict[str, Any] = {
        "model": model_name,
        "stream_usage": True,
        **cap_kwargs,
    }
    if not drop_temperature:
        kwargs["temperature"] = 0
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    elif cls is ChatOpenAI:
        # ChatOpenAI is the wrapper for every OpenAI-compatible provider in our
        # PROVIDER_MAP (openai itself, OpenRouter, generic openai_compatible).
        # The underlying OpenAI Python SDK falls back to ``OPENAI_BASE_URL``
        # env when ``base_url`` is omitted, which routes the request to the
        # *wrong* host whenever a shell has set it for an unrelated tool
        # (Claude Code / codex helpers do this). Pin the canonical endpoint
        # per-provider so each credential reaches its own gateway.
        default_base = _OPENAI_FAMILY_BASE_URLS.get(provider)
        if default_base:
            kwargs["base_url"] = default_base

    if cls in (ChatOpenAI,):
        kwargs["http_async_client"] = httpx.AsyncClient(verify=_ssl_ctx)
        kwargs["http_client"] = httpx.Client(verify=_ssl_ctx)

    return cls(**kwargs)


# Default base URL per OpenAI-compatible provider. Used only when the caller
# (credential payload, model row, or preview body) didn't supply one.
_OPENAI_FAMILY_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    # ``openai_compatible`` is a self-hosted catch-all; we never know the
    # right host without the credential telling us, so don't guess.
}


# ---------------------------------------------------------------------------
# Model fallback (M10)
# ---------------------------------------------------------------------------


# Recoverable error classes — fall back on the next model in the chain.
# Keep this list narrow: a programming error (e.g., ``TypeError``) should
# still surface so we don't silently mask bugs as fallbacks.
_FALLBACK_RECOVERABLE_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    httpx.HTTPError,
    httpx.TimeoutException,
    ConnectionError,
)


_FALLBACK_RECOVERABLE_STATUS = frozenset({401, 403, 404, 408, 409, 429, 500, 502, 503, 504})


def _is_fallback_recoverable(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` looks worth a fallback retry.

    Provider SDKs surface auth / rate / outage failures as ``HTTPStatusError``
    (with a ``response.status_code``) or as their own typed wrappers (e.g.
    ``openai.AuthenticationError``). We unify on:

    1. ``status_code`` attribute / nested response — accept the canonical 4xx
       and 5xx codes from the LiteLLM fallback set.
    2. Subclasses of the recoverable type tuple.
    3. The string fallback (``"timeout"`` / ``"unauthorized"`` etc.) is
       deliberately *not* tried — relying on message strings is fragile and
       has bitten us before.
    """

    if isinstance(exc, _FALLBACK_RECOVERABLE_TYPES):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    return bool(isinstance(status, int) and status in _FALLBACK_RECOVERABLE_STATUS)


async def _resolve_model_for_fallback(
    db: AsyncSession,
    model_id: uuid.UUID,
) -> tuple[str, str, str | None] | None:
    """Look up a fallback model row → ``(provider, model_name, base_url)``.

    Returns ``None`` when the row is missing so the walker can skip it
    instead of crashing the whole chain.
    """

    from app.models.model import Model as ModelRow

    result = await db.execute(select(ModelRow).where(ModelRow.id == model_id))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return row.provider, row.model_name, getattr(row, "base_url", None)


async def _audit_fallback_attempt(
    db: AsyncSession,
    *,
    agent: Agent,
    model_id: uuid.UUID | None,
    provider: str,
    model_name: str,
    error: BaseException | None,
    success: bool,
) -> None:
    """Record one ``fallback`` step on the credential audit log.

    The credential is the agent's ``llm_credential`` — fallbacks reuse the
    same key. We swallow audit failures because the agent run must succeed
    even if the audit DB is misbehaving.
    """

    if agent.llm_credential_id is None:
        return
    try:
        from app.credentials import service as credential_service

        metadata: dict[str, Any] = {
            "phase": "attempt",
            "provider": provider,
            "model_name": model_name,
            "success": success,
            "agent_id": str(agent.id),
        }
        if model_id is not None:
            metadata["model_id"] = str(model_id)
        await credential_service.write_audit_log(
            db,
            credential_id=agent.llm_credential_id,
            actor_user_id=agent.user_id,
            action="fallback",
            source="runtime",
            error=str(error) if error else None,
            metadata=metadata,
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — never break the runtime on audit
        logger.warning("fallback audit log write failed", exc_info=True)


async def create_chat_model_with_fallback(
    agent: Agent,
    db: AsyncSession,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    **extra: object,
) -> BaseChatModel:
    """Build a chat model, walking ``agent.model_fallback_list`` on failure.

    The function attempts the primary ``agent.model`` first. If construction
    succeeds the result is returned immediately (we don't probe the model on
    every request — that's the health check's job). If construction raises a
    recoverable error, each fallback model id is tried in order and the
    first one to instantiate cleanly wins. Every attempt — primary plus
    each fallback — writes one ``fallback`` audit row when the agent has a
    bound credential. The ``api_key`` / ``base_url`` arguments are reused
    across the chain because the fallback list is "same key, different
    model".

    Backward compatible with :func:`create_chat_model`: when an agent has no
    fallback list the call is a thin wrapper around the primary path.
    """

    primary = agent.model
    if primary is None:
        raise ValueError("agent.model relationship not loaded")

    primary_provider = primary.provider
    primary_name = primary.model_name
    primary_base = base_url or getattr(primary, "base_url", None)

    fallback_ids: list[uuid.UUID] = []
    if agent.model_fallback_list:
        for raw in agent.model_fallback_list:
            try:
                fallback_ids.append(uuid.UUID(str(raw)))
            except (TypeError, ValueError):
                logger.warning("ignoring non-UUID fallback id: %r", raw)

    last_error: BaseException | None = None

    # 1) Primary attempt.
    try:
        model = create_chat_model(
            primary_provider,
            primary_name,
            api_key=api_key,
            base_url=primary_base,
            **extra,
        )
        if fallback_ids:
            await _audit_fallback_attempt(
                db,
                agent=agent,
                model_id=primary.id,
                provider=primary_provider,
                model_name=primary_name,
                error=None,
                success=True,
            )
        return model
    except Exception as exc:  # noqa: BLE001 — fall through to retries
        last_error = exc
        logger.info(
            "primary model %s/%s failed; attempting fallback chain (n=%d)",
            primary_provider,
            primary_name,
            len(fallback_ids),
        )
        await _audit_fallback_attempt(
            db,
            agent=agent,
            model_id=primary.id,
            provider=primary_provider,
            model_name=primary_name,
            error=exc,
            success=False,
        )
        if not fallback_ids or not _is_fallback_recoverable(exc):
            raise

    # 2) Walk fallbacks in order.
    for fallback_id in fallback_ids:
        resolved = await _resolve_model_for_fallback(db, fallback_id)
        if resolved is None:
            logger.warning("fallback model id missing: %s — skipping", fallback_id)
            continue
        provider, model_name, fb_base = resolved
        try:
            model = create_chat_model(
                provider,
                model_name,
                api_key=api_key,
                base_url=fb_base,
                **extra,
            )
            await _audit_fallback_attempt(
                db,
                agent=agent,
                model_id=fallback_id,
                provider=provider,
                model_name=model_name,
                error=None,
                success=True,
            )
            return model
        except Exception as exc:  # noqa: BLE001 — try next
            last_error = exc
            await _audit_fallback_attempt(
                db,
                agent=agent,
                model_id=fallback_id,
                provider=provider,
                model_name=model_name,
                error=exc,
                success=False,
            )
            if not _is_fallback_recoverable(exc):
                raise

    # 3) Everything failed — re-raise the most recent error.
    assert last_error is not None
    raise last_error


__all__ = [
    "PROVIDER_MAP",
    "create_chat_model",
    "create_chat_model_for_test",
    "create_chat_model_with_fallback",
    "env_provider_keys",
]
