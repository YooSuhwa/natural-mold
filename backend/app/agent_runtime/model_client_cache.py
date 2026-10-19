"""Shared chat-model instance cache, cache keys, client cleanup and SSL context."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import ssl
from collections import OrderedDict
from collections.abc import Awaitable, Mapping
from pathlib import Path
from typing import Any

import certifi
from langchain_core.language_models import BaseChatModel

logger = logging.getLogger("app.agent_runtime.model_factory")

_MODEL_CACHE_MAXSIZE = 64
_MODEL_CACHE: OrderedDict[tuple[Any, ...], BaseChatModel] = OrderedDict()


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
_hc_cert = Path(os.path.expanduser("~/.ssl/HC_SSL.pem"))  # noqa: PTH111 — Path.expanduser crashes on unresolvable $HOME
try:
    import truststore

    _ssl_ctx: ssl.SSLContext = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:  # pragma: no cover — runtime dep, but defensive
    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
if _hc_cert.exists():
    _ssl_ctx.load_verify_locations(_hc_cert)
