"""Chat model construction and provider quirk helpers (ADR-014).

``ChatOpenAI`` is read from the ``model_factory`` facade at call time so tests
that patch ``model_factory.ChatOpenAI`` keep observing the replacement.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from app.agent_runtime.model_client_cache import (
    _MODEL_CACHE,
    _MODEL_CACHE_MAXSIZE,
    _close_cached_model,
    _model_cache_key,
)
from app.agent_runtime.model_facade_bindings import model_attr
from app.agent_runtime.model_provider_registry import (
    _ENV_FALLBACK,
    _GPT5_DEFAULT_COMPLETION_TOKENS,
    _OPENAI_FAMILY_BASE_URLS,
    PROVIDER_MAP,
    TEST_COMPLETION_TOKEN_CAP,
    is_gpt5_family,
)
from app.outbound.client import outbound_http_client, outbound_sync_http_client

logger = logging.getLogger("app.agent_runtime.model_factory")


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
        # Provider chat models expose this optional runtime attribute dynamically.
        setattr(model, "profile", {**existing, "max_input_tokens": cw})  # noqa: B010
    except Exception:  # noqa: BLE001 — profile write is best-effort
        logger.debug("context_window profile injection failed", exc_info=True)


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

    default_cls: type[BaseChatModel] = model_attr("ChatOpenAI")
    cls = PROVIDER_MAP.get(provider, default_cls)

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

    default_cls: type[BaseChatModel] = model_attr("ChatOpenAI")
    cls = PROVIDER_MAP.get(provider, default_cls)

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

    if cls is model_attr("ChatOpenAI"):
        # 11.9: the shared outbound constructors (httpx default 5s; the SDK sets its own).
        kwargs["http_async_client"] = outbound_http_client(timeout=5.0)
        kwargs["http_client"] = outbound_sync_http_client(timeout=5.0)
