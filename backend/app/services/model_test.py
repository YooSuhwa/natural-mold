"""Probe a model end-to-end with a single decrypted Credential.

The "test" surface is purposely cheap (``max_tokens=10``) and deterministic
(``temperature=0``) — its job is to prove a key + base_url + model_name combo
will round-trip a real provider call, not to evaluate quality. The same code
path drives the ``POST /api/models/{id}/test`` "registered" flow and the
``POST /api/models/test-preview`` "before save" flow so the response shape is
identical from the UI's perspective.

The single-shot probe + clean-error + curl-reproduction pattern is borrowed
from prior art in the LiteLLM proxy ``/health/test_connection`` handler and
its dashboard companion (see ``NOTICES.md``). Implementation, identifiers,
and string contents are Moldy-native; we never copy LiteLLM code.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from app.agent_runtime.model_factory import create_chat_model_for_test

logger = logging.getLogger(__name__)


# Hard cap on the round-trip — the UI gets a deterministic ceiling and the
# server doesn't burn a slot waiting on a stalled provider.
_TEST_TIMEOUT_SECONDS = 30.0
# Cap the visible response so a chatty model can't blow up the JSON payload.
_RESPONSE_PREVIEW_LIMIT = 240
# Same idea for raw provider errors (bag truncation > full stack trace).
_ERROR_MESSAGE_LIMIT = 300

# The canonical probe prompt. Short, deterministic, and obviously a test.
_TEST_PROMPT = "Reply with just the word 'pong'."

ErrorKind = Literal["auth", "not_found", "rate_limit", "timeout", "other"]


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class ModelTestError:
    """Structured failure detail. ``raw`` keeps the original line for debugging."""

    kind: ErrorKind
    message: str
    raw: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "raw": self.raw}


@dataclass
class ModelTestResult:
    """End-to-end probe result.

    ``raw_request`` and ``curl_command`` are reconstructed (provider SDKs
    intentionally do not expose the wire request); fields are masked so the
    payload is safe to surface in the UI clipboard.
    """

    success: bool
    response: str | None = None
    latency_ms: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    estimated_cost_usd: float | None = None
    error: ModelTestError | None = None
    raw_request: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    curl_command: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "response": self.response,
            "latency_ms": self.latency_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "estimated_cost_usd": self.estimated_cost_usd,
            "error": self.error.to_dict() if self.error else None,
            "raw_request": self.raw_request,
            "raw_response": self.raw_response,
            "curl_command": self.curl_command,
            "metadata": self.metadata or None,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_model_test(
    *,
    provider: str,
    model_name: str,
    base_url: str | None,
    credential_data: dict[str, Any],
    cost_per_input_token: Decimal | None = None,
    cost_per_output_token: Decimal | None = None,
) -> ModelTestResult:
    """Send a single short prompt to ``provider:model_name`` and report.

    Required:
    - ``provider``        canonical provider key (openai / anthropic / google
                          / openrouter / openai_compatible / custom).
    - ``model_name``      model id as the provider expects on the wire.
    - ``base_url``        override; ``None`` falls back to provider default.
    - ``credential_data`` decrypted credential payload (e.g. ``{"api_key": ...}``).

    The optional ``cost_per_*`` fields scale token counts to dollars. Caller
    looks them up on the ``Model`` row when available; preview mode passes
    ``None`` and the field comes back ``None`` in the response.
    """

    api_key = _extract_api_key(credential_data)
    raw_request = _reconstruct_request(
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
    )
    # The curl command never sees the real key — placeholder substitution is
    # done before the dict is rendered so even an exception path can't leak it.
    curl_command = _build_curl(raw_request)

    try:
        chat_model = create_chat_model_for_test(
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
        )
    except Exception as exc:  # noqa: BLE001 — provider SDK init failures
        logger.warning(
            "create_chat_model_for_test failed: provider=%s model=%s err=%r",
            provider,
            model_name,
            exc,
        )
        return ModelTestResult(
            success=False,
            latency_ms=0,
            error=_classify(exc),
            raw_request=raw_request,
            curl_command=curl_command,
        )

    return await _invoke_with_timeout(
        chat_model=chat_model,
        raw_request=raw_request,
        curl_command=curl_command,
        cost_per_input_token=cost_per_input_token,
        cost_per_output_token=cost_per_output_token,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _invoke_with_timeout(
    *,
    chat_model: BaseChatModel,
    raw_request: dict[str, Any],
    curl_command: str,
    cost_per_input_token: Decimal | None,
    cost_per_output_token: Decimal | None,
) -> ModelTestResult:
    started = time.monotonic()

    try:
        response = await asyncio.wait_for(
            chat_model.ainvoke([HumanMessage(content=_TEST_PROMPT)]),
            timeout=_TEST_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return ModelTestResult(
            success=False,
            latency_ms=latency_ms,
            error=ModelTestError(
                kind="timeout",
                message=f"request exceeded {_TEST_TIMEOUT_SECONDS:.0f}s timeout",
                raw=str(exc) or None,
            ),
            raw_request=raw_request,
            curl_command=curl_command,
        )
    except Exception as exc:  # noqa: BLE001 — provider error funnel
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "model test failed: provider_error=%r latency_ms=%s",
            exc,
            latency_ms,
        )
        return ModelTestResult(
            success=False,
            latency_ms=latency_ms,
            error=_classify(exc),
            raw_request=raw_request,
            curl_command=curl_command,
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    text = _extract_text(response)
    usage = getattr(response, "usage_metadata", None) or {}
    tokens_in = _coerce_int(usage.get("input_tokens"))
    tokens_out = _coerce_int(usage.get("output_tokens"))
    estimated = _estimate_cost(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_per_input_token=cost_per_input_token,
        cost_per_output_token=cost_per_output_token,
    )

    return ModelTestResult(
        success=True,
        response=text[:_RESPONSE_PREVIEW_LIMIT] if text else None,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated_cost_usd=estimated,
        raw_request=raw_request,
        raw_response={"content": text, "usage": dict(usage)},
        curl_command=curl_command,
    )


# ---- Error classification --------------------------------------------------


_AUTH_HINTS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "permission denied",
    "forbidden",
    "no auth credentials found",
)
_NOT_FOUND_HINTS = (
    "model not found",
    "the model `",
    "does not exist",
    "no such model",
    "unknown model",
)
_RATE_LIMIT_HINTS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota",
)


def _classify(exc: Exception) -> ModelTestError:
    """Bucket a provider exception into one of the five known kinds."""

    raw = str(exc) or exc.__class__.__name__
    cleaned = _clean_error_message(raw)
    status = _status_code_of(exc)
    lower = raw.lower()

    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return ModelTestError(kind="timeout", message=cleaned, raw=raw)

    if status == 401 or status == 403 or any(h in lower for h in _AUTH_HINTS):
        return ModelTestError(kind="auth", message=cleaned, raw=raw)
    if status == 404 or any(h in lower for h in _NOT_FOUND_HINTS):
        return ModelTestError(kind="not_found", message=cleaned, raw=raw)
    if status == 429 or any(h in lower for h in _RATE_LIMIT_HINTS):
        return ModelTestError(kind="rate_limit", message=cleaned, raw=raw)

    return ModelTestError(kind="other", message=cleaned, raw=raw)


_PROVIDER_PREFIX_RE = re.compile(
    r"^(litellm|openai|anthropic|google|httpx)\.[A-Za-z]*Error:\s*",
    re.IGNORECASE,
)
_ERROR_CODE_PREFIX_RE = re.compile(r"^Error\s+code:\s+\d+\s+-\s+", re.IGNORECASE)


def _clean_error_message(raw: str) -> str:
    """Strip provider noise, stack traces, and hard-cap the length."""

    if not raw:
        return "unknown error"
    cleaned = raw.split("stack trace:")[0].strip()
    cleaned = _PROVIDER_PREFIX_RE.sub("", cleaned)
    cleaned = _ERROR_CODE_PREFIX_RE.sub("", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > _ERROR_MESSAGE_LIMIT:
        cleaned = cleaned[: _ERROR_MESSAGE_LIMIT - 1].rstrip() + "…"
    return cleaned or "unknown error"


def _status_code_of(exc: Exception) -> int | None:
    """Best-effort extraction of an HTTP status code from a provider error."""

    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


# ---- Request reconstruction + curl rendering -------------------------------


_REDACTED = "***"
_CURL_PLACEHOLDER = "${API_KEY}"


def _extract_api_key(data: dict[str, Any]) -> str | None:
    """Pull whatever the credential payload calls its primary auth secret."""

    for candidate in ("api_key", "token", "access_token", "secret"):
        value = data.get(candidate)
        if isinstance(value, str) and value:
            return value
    return None


def _reconstruct_request(
    *,
    provider: str,
    model_name: str,
    base_url: str | None,
    api_key: str | None,
) -> dict[str, Any]:
    """Best-effort wire-shape reconstruction (the SDK doesn't expose it).

    Headers carry ``***`` for the secret so the dict is safe to surface in
    debug toggles without leaking the key. ``_build_curl`` rewrites the
    placeholder to ``${API_KEY}`` for shell-pasteable reproduction.
    """

    url, headers, body = _provider_wire_shape(
        provider=provider,
        model_name=model_name,
        base_url=base_url,
        api_key=api_key,
    )
    return {
        "url": url,
        "method": "POST",
        "headers": headers,
        "body": body,
    }


# OpenAI's GPT-5 / o-series families reject ``max_tokens`` and ``temperature``
# overrides — the wire payload uses ``max_completion_tokens`` instead. We
# mirror the runtime decision here so ``raw_request`` / curl reflect the
# bytes actually sent rather than the legacy shape.
_GPT5_FAMILY_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")


def _is_gpt5_family(provider: str, model_name: str) -> bool:
    if provider != "openai":
        return False
    name = (model_name or "").lower()
    return any(name.startswith(p) for p in _GPT5_FAMILY_PREFIXES)


def _provider_wire_shape(
    *,
    provider: str,
    model_name: str,
    base_url: str | None,
    api_key: str | None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    is_gpt5 = _is_gpt5_family(provider, model_name)

    body: dict[str, Any] = {
        "model": model_name,
        "messages": [{"role": "user", "content": _TEST_PROMPT}],
    }
    if is_gpt5:
        # GPT-5 family — new field name, no temperature override.
        body["max_completion_tokens"] = 10
    else:
        body["max_tokens"] = 10
        body["temperature"] = 0
    headers: dict[str, str] = {"Content-Type": "application/json"}

    if provider == "anthropic":
        url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        headers["x-api-key"] = _REDACTED if api_key else ""
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "google":
        # Gemini puts the model in the path and the key in the query string.
        host = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        url = f"{host}/models/{model_name}:generateContent?key={_REDACTED}"
        body = {
            "contents": [{"role": "user", "parts": [{"text": _TEST_PROMPT}]}],
            "generationConfig": {"maxOutputTokens": 10, "temperature": 0},
        }
    else:
        # openai / openrouter / openai_compatible / custom — all chat completions.
        host = (base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{host}/chat/completions"
        headers["Authorization"] = f"Bearer {_REDACTED}" if api_key else ""

    return url, headers, body


def _build_curl(request: dict[str, Any]) -> str:
    """Render a copy-pasteable curl command with ``${API_KEY}`` placeholder."""

    headers = dict(request.get("headers") or {})
    for key in list(headers):
        if key.lower() == "authorization":
            headers[key] = f"Bearer {_CURL_PLACEHOLDER}"
        elif key.lower() in {"x-api-key", "api-key"}:
            headers[key] = _CURL_PLACEHOLDER

    url = str(request.get("url") or "")
    # The Google-style URL inlines ?key=*** — swap it for the placeholder too.
    url = url.replace(f"key={_REDACTED}", f"key={_CURL_PLACEHOLDER}")

    method = str(request.get("method") or "POST").upper()
    body = request.get("body") or {}

    import json as _json

    body_str = _json.dumps(body, ensure_ascii=False, indent=2)
    header_lines = "".join(
        f"  -H '{name}: {value}' \\\n"
        for name, value in headers.items()
        if value
    )
    return (
        f"curl -X {method} '{url}' \\\n"
        f"{header_lines}"
        f"  -d '{body_str}'"
    )


# ---- Misc helpers ----------------------------------------------------------


def _extract_text(response: Any) -> str:
    """Coerce ``ainvoke`` output into a flat string."""

    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # LangChain returns a list of content blocks for multi-modal output.
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _estimate_cost(
    *,
    tokens_in: int | None,
    tokens_out: int | None,
    cost_per_input_token: Decimal | None,
    cost_per_output_token: Decimal | None,
) -> float | None:
    if cost_per_input_token is None and cost_per_output_token is None:
        return None
    total = Decimal("0")
    if tokens_in and cost_per_input_token is not None:
        total += Decimal(tokens_in) * cost_per_input_token
    if tokens_out and cost_per_output_token is not None:
        total += Decimal(tokens_out) * cost_per_output_token
    return float(total)


__all__ = [
    "ModelTestError",
    "ModelTestResult",
    "run_model_test",
]
