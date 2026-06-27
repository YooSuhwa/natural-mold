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

import asyncio
import hashlib
import inspect
import json
import logging
import os
import ssl
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Mapping
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

_MODEL_CACHE_MAXSIZE = 64
_MODEL_CACHE: OrderedDict[tuple[Any, ...], BaseChatModel] = OrderedDict()


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


def _api_key_fingerprint(api_key: str | None) -> str | None:
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _model_cache_key(
    *,
    provider: str,
    model_name: str,
    resolved_key: str | None,
    base_url: str | None,
    kwargs: dict[str, Any],
    context_window: int | None = None,
) -> tuple[Any, ...]:
    key_kwargs = {
        k: v for k, v in kwargs.items() if k not in {"api_key", "http_async_client", "http_client"}
    }
    return (
        provider,
        model_name,
        base_url,
        _api_key_fingerprint(resolved_key),
        # ``context_window`` is injected post-construction into ``model.profile``
        # (not a constructor kwarg), so it must be part of the key to keep
        # models built with different windows in distinct cache slots — a
        # tiny-window test build must not poison the production instance.
        context_window,
        json.dumps(_jsonable(key_kwargs), sort_keys=True, separators=(",", ":")),
    )


def _apply_context_window_profile(model: BaseChatModel, context_window: int | None) -> None:
    """Inject our DB ``context_window`` into the LangChain model ``profile``.

    deepagents auto-injects ``SummarizationMiddleware``; its
    ``compute_summarization_defaults`` reads ``model.profile["max_input_tokens"]``
    to pick the trigger. Profile-less models (``openai_compatible``/custom
    gateways) otherwise fall back to a fixed ``("tokens", 170000)`` threshold
    that is unrelated to the real limit. Writing our single-source-of-truth
    window flips them onto the model-aware ``("fraction", 0.85)`` path and keeps
    the chat context gauge and the auto-compaction threshold on the same number.

    ``model.profile`` is a writable Pydantic field (verified for ChatOpenAI /
    ChatAnthropic / ChatGoogleGenerativeAI); failures are swallowed defensively
    so a profile quirk never breaks model construction.
    """

    if not context_window:
        return
    try:
        cw = int(context_window)
    except (TypeError, ValueError):
        return
    if cw <= 0:
        return
    try:
        existing = getattr(model, "profile", None) or {}
        model.profile = {**existing, "max_input_tokens": cw}
    except Exception:  # noqa: BLE001 — profile write is best-effort
        logger.debug("context_window profile injection failed", exc_info=True)


async def _await_close_result(result: Awaitable[Any]) -> None:
    await result


def _close_maybe_async(value: Any) -> None:
    if value is None:
        return
    for method_name in ("close", "aclose"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
        except Exception:  # noqa: BLE001
            logger.debug("model cache client close failed", exc_info=True)
            continue
        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(_await_close_result(result))
            else:
                loop.create_task(_await_close_result(result))
        return


def _close_cached_model(model: BaseChatModel) -> None:
    for attr in ("http_async_client", "http_client", "async_client", "client"):
        _close_maybe_async(getattr(model, attr, None))


def clear_model_cache() -> None:
    """Clear cached model instances and close owned HTTP clients when present."""

    while _MODEL_CACHE:
        _, model = _MODEL_CACHE.popitem()
        _close_cached_model(model)


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
    *,
    allow_env_fallback: bool = True,
    **extra: object,
) -> BaseChatModel:
    """Build a LangChain chat model for ``provider``.

    ``api_key`` is the resolved (decrypted) key from the caller. When ``None``
    the env-var fallback is consulted for ``openai``/``anthropic``/``google``/
    ``openrouter`` only when ``allow_env_fallback`` is true. User-facing
    runtime paths pass ``False`` where falling through to system credentials
    would cross the credential boundary.

    Provider quirk 처리는 helper 함수 (``_apply_*``) 로 분리 — ADR-014.
    """

    cls = PROVIDER_MAP.get(provider, ChatOpenAI)

    # ``context_window`` is consumed here (injected into ``model.profile`` for
    # the auto-summarization threshold), not forwarded to the model constructor.
    context_window = extra.pop("context_window", None)

    fallback_key = _ENV_FALLBACK.get(provider) if allow_env_fallback else None
    resolved_key = api_key or fallback_key or None
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

    _apply_anthropic_quirks(provider, kwargs)
    _apply_gpt5_quirks(
        provider, model_name, kwargs, completion_token_default=_GPT5_DEFAULT_COMPLETION_TOKENS
    )
    _apply_openai_compatible_base_url(provider, kwargs)

    kwargs["stream_usage"] = True
    cw = int(context_window) if isinstance(context_window, int) and context_window > 0 else None
    cache_key = _model_cache_key(
        provider=provider,
        model_name=model_name,
        resolved_key=resolved_key,
        base_url=base_url,
        kwargs=kwargs,
        context_window=cw,
    )
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None:
        _MODEL_CACHE.move_to_end(cache_key)
        return cached

    _apply_openai_ssl_clients(cls, kwargs)

    model = cls(**kwargs)
    _apply_context_window_profile(model, cw)
    _MODEL_CACHE[cache_key] = model
    _MODEL_CACHE.move_to_end(cache_key)
    while len(_MODEL_CACHE) > _MODEL_CACHE_MAXSIZE:
        _, evicted = _MODEL_CACHE.popitem(last=False)
        _close_cached_model(evicted)
    return model


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


def create_chat_model_for_test(
    provider: str,
    model_name: str,
    *,
    api_key: str | None,
    base_url: str | None = None,
) -> BaseChatModel:
    """Build a deterministic, low-cost LangChain chat model for the test surface.

    Locked-in defaults (``TEST_COMPLETION_TOKEN_CAP``, ``temperature=0``)
    keep test invocations cheap and reproducible no matter what model row
    exists in the catalog. The caller is expected to wrap the resulting
    ``ainvoke`` in an ``asyncio.wait_for(...)`` to enforce the timeout —
    this factory does not schedule timers itself.

    Provider quirk 처리는 ``create_chat_model`` 과 동일 helper 사용 — ADR-014.
    """

    cls = PROVIDER_MAP.get(provider, ChatOpenAI)

    kwargs: dict[str, Any] = {
        "model": model_name,
        "stream_usage": True,
        "max_tokens": TEST_COMPLETION_TOKEN_CAP,
        "temperature": 0,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    _apply_gpt5_quirks(
        provider, model_name, kwargs, completion_token_default=TEST_COMPLETION_TOKEN_CAP
    )
    _apply_openai_compatible_base_url(provider, kwargs)
    _apply_openai_ssl_clients(cls, kwargs)

    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Provider quirk helpers (ADR-014)
# ---------------------------------------------------------------------------


def _apply_anthropic_quirks(provider: str, kwargs: dict[str, Any]) -> None:
    """Anthropic API 가 ``temperature`` 와 ``top_p`` 동시 지정을 거부 — top_p drop."""

    if provider == "anthropic" and "temperature" in kwargs and "top_p" in kwargs:
        kwargs.pop("top_p")


def _apply_gpt5_quirks(
    provider: str,
    model_name: str,
    kwargs: dict[str, Any],
    *,
    completion_token_default: int,
) -> None:
    """OpenAI GPT-5 / o-series 가족: ``max_tokens`` 거부 + non-default ``temperature`` 거부.

    - ``max_tokens`` → ``max_completion_tokens`` 로 top-level forward
      (langchain-openai 0.3+ 는 ``model_kwargs`` 안에 넣으면 UserWarning 후
      제거해 OpenAI 에 도달 못 함).
    - caller 가 cap 을 안 줬으면 ``completion_token_default`` 보장 — reasoning
      토큰을 다 쓰고 visible content 가 비는 회귀 방지.
    - ``temperature`` 는 모델이 default 만 받으므로 drop.
    """

    if not is_gpt5_family(provider, model_name):
        return
    max_tokens = kwargs.pop("max_tokens", None)
    kwargs.pop("temperature", None)
    cap = max_tokens if isinstance(max_tokens, int) else completion_token_default
    kwargs.setdefault("max_completion_tokens", cap)


def _apply_openai_compatible_base_url(provider: str, kwargs: dict[str, Any]) -> None:
    """``base_url`` 미지정 시 provider canonical endpoint pin.

    ChatOpenAI 가 base_url 없을 때 OpenAI Python SDK 가 ``OPENAI_BASE_URL``
    env 로 fallback. 사용자 셸이 RunPod proxy / Claude Code helper / 사내
    프록시로 export 해놓으면 *엉뚱한* 호스트로 라우팅되어 404 회귀
    (e.g. ``OPENAI_BASE_URL=https://*.proxy.runpod.net/v1`` 환경에서 gpt-5
    호출 시 404). provider 별 canonical endpoint 명시 set 으로 OS env 우회 차단.
    """

    if "base_url" in kwargs:
        return
    default = _OPENAI_FAMILY_BASE_URLS.get(provider)
    if default:
        kwargs["base_url"] = default


def _apply_openai_ssl_clients(cls: type[BaseChatModel], kwargs: dict[str, Any]) -> None:
    """ChatOpenAI 계열만 truststore 기반 SSL client 주입 (사내 VPN / corp proxy 호환)."""

    if cls is ChatOpenAI:
        kwargs["http_async_client"] = httpx.AsyncClient(verify=_ssl_ctx)
        kwargs["http_client"] = httpx.Client(verify=_ssl_ctx)


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

    model_extra: dict[str, Any] = dict(extra)
    allow_env_fallback_raw = model_extra.pop("allow_env_fallback", True)
    if not isinstance(allow_env_fallback_raw, bool):
        raise TypeError("allow_env_fallback must be a bool")
    allow_env_fallback = allow_env_fallback_raw

    last_error: BaseException | None = None

    # 1) Primary attempt.
    try:
        model = create_chat_model(
            primary_provider,
            primary_name,
            api_key=api_key,
            base_url=primary_base,
            allow_env_fallback=allow_env_fallback,
            **model_extra,
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
                allow_env_fallback=allow_env_fallback,
                **model_extra,
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
