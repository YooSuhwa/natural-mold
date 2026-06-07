from __future__ import annotations

import logging
import os
import re
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_CLIENT: Any | None = None
_CLIENT_KEY: tuple[Any, ...] | None = None
_SECRET_VALUE_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "token",
)
_TOKEN_RE = re.compile(r"\b(sk|pk|pat|Bearer)[-_A-Za-z0-9]{12,}\b")
_HEX_SECRET_RE = re.compile(r"\b[a-fA-F0-9]{32,}\b")


@dataclass(frozen=True)
class LangfuseTraceRecord:
    provider: str
    trace_id: str
    trace_url: str | None = None


@dataclass
class LangfuseRunContext:
    enabled: bool
    trace: LangfuseTraceRecord | None = None
    callback: Any | None = None
    client: Any | None = None
    metadata: dict[str, Any] | None = None
    tags: list[str] | None = None
    root_name: str = "agent.chat"

    def configure_config(self, config: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or self.callback is None:
            return config

        merged = dict(config)
        merged_callbacks = list(merged.get("callbacks") or [])
        merged_callbacks.append(self.callback)
        merged["callbacks"] = merged_callbacks
        merged["metadata"] = {**(merged.get("metadata") or {}), **(self.metadata or {})}

        merged_tags = list(merged.get("tags") or [])
        for tag in self.tags or []:
            if tag not in merged_tags:
                merged_tags.append(tag)
        merged["tags"] = merged_tags
        return merged

    def activate(
        self,
        *,
        input_payload: Any | None = None,
        output_payload: Any | None = None,
    ) -> AbstractContextManager[Any]:
        if not self.enabled or self.client is None or self.trace is None:
            return nullcontext()
        if not hasattr(self.client, "start_as_current_observation"):
            return nullcontext()

        payload = redact_payload(input_payload)
        if not settings.langfuse_capture_input_output:
            payload = "[redacted: input/output capture disabled]"

        kwargs: dict[str, Any] = {
            "as_type": "span",
            "name": self.root_name,
            "trace_context": {"trace_id": self.trace.trace_id},
        }
        if payload is not None:
            kwargs["input"] = payload
        if output_payload is not None:
            kwargs["output"] = redact_payload(output_payload)

        try:
            return self.client.start_as_current_observation(**kwargs)
        except Exception:
            logger.warning("Langfuse root span creation failed", exc_info=True)
            return nullcontext()

    def flush(self) -> None:
        if not self.enabled or self.client is None:
            return
        flusher = getattr(self.client, "flush", None)
        if not callable(flusher):
            return
        try:
            flusher()
        except Exception:
            logger.warning("Langfuse flush failed", exc_info=True)


def _has_langfuse_config() -> bool:
    return bool(
        settings.langfuse_public_key
        and settings.langfuse_secret_key
        and _langfuse_base_url()
    )


def is_langfuse_enabled() -> bool:
    """Return whether trace capture should run for the current settings."""

    if settings.langfuse_enabled is False:
        return False
    return _has_langfuse_config()


def _has_langfuse_credentials() -> bool:
    return bool(is_langfuse_enabled() and _has_langfuse_config())


def _langfuse_base_url() -> str:
    return settings.langfuse_base_url or os.getenv("LANGFUSE_HOST", "")


def _sample_rate() -> float:
    try:
        return max(0.0, min(1.0, float(settings.langfuse_sample_rate)))
    except (TypeError, ValueError):
        return 1.0


def _client_key() -> tuple[Any, ...]:
    return (
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
        _langfuse_base_url(),
        _sample_rate(),
        settings.langfuse_redaction_enabled,
        settings.langfuse_capture_input_output,
    )


def _get_langfuse_client() -> Any:
    """Create or reuse the Langfuse SDK client.

    Kept behind a tiny adapter so SDK argument changes do not leak through
    the runtime. The constructor shape is from the current Langfuse Python SDK
    docs: public/secret/base_url, sample_rate, and mask.
    """

    global _CLIENT, _CLIENT_KEY

    key = _client_key()
    if _CLIENT is not None and key == _CLIENT_KEY:
        return _CLIENT

    from langfuse import Langfuse

    kwargs: dict[str, Any] = {
        "public_key": settings.langfuse_public_key,
        "secret_key": settings.langfuse_secret_key,
        "base_url": _langfuse_base_url(),
        "sample_rate": _sample_rate(),
    }
    if settings.langfuse_redaction_enabled or not settings.langfuse_capture_input_output:
        kwargs["mask"] = redact_payload

    try:
        _CLIENT = Langfuse(**kwargs)
    except TypeError:
        kwargs["host"] = kwargs.pop("base_url")
        _CLIENT = Langfuse(**kwargs)
    _CLIENT_KEY = key
    return _CLIENT


def _build_callback_handler(*, trace_id: str) -> Any:
    from langfuse.langchain import CallbackHandler

    return CallbackHandler(
        public_key=settings.langfuse_public_key,
        update_trace=True,
        trace_context={"trace_id": trace_id},
    )


def _trace_id_for_run(client: Any, run_id: str) -> str:
    creator = getattr(client, "create_trace_id", None)
    if callable(creator):
        return str(creator(seed=run_id))

    try:
        from langfuse import Langfuse

        return Langfuse.create_trace_id(seed=run_id)
    except Exception:
        return run_id.replace("-", "")[:32].ljust(32, "0")


def _trace_url_for_id(client: Any, trace_id: str) -> str | None:
    getter = getattr(client, "get_trace_url", None)
    if callable(getter):
        try:
            trace_url = getter(trace_id=trace_id)
            return str(trace_url) if trace_url is not None else None
        except Exception:
            logger.debug("Langfuse get_trace_url failed", exc_info=True)

    base = _langfuse_base_url().rstrip("/")
    if not base:
        return None
    project = settings.langfuse_project.strip().strip("/")
    if project:
        return f"{base}/project/{project}/traces/{trace_id}"
    return f"{base}/traces/{trace_id}"


def _tags_for_run(cfg: Any, *, source: str) -> list[str]:
    tags = ["moldy", "agent-chat", f"source:{source}"]
    agent_id = getattr(cfg, "agent_id", None)
    if agent_id:
        tags.append(f"agent:{agent_id}")
    return tags


def _metadata_for_run(cfg: Any, *, run_id: str, source: str) -> dict[str, Any]:
    user_id = getattr(cfg, "user_id", None)
    conversation_id = getattr(cfg, "thread_id", None)
    agent_id = getattr(cfg, "agent_id", None)
    agent_name = getattr(cfg, "agent_name", None)
    model_id = getattr(cfg, "model_id", None)
    checkpoint_id = getattr(cfg, "checkpoint_id", None)
    return {
        "langfuse_user_id": str(user_id) if user_id else None,
        "langfuse_session_id": str(conversation_id) if conversation_id else None,
        "langfuse_tags": _tags_for_run(cfg, source=source),
        "moldy_user_id": str(user_id) if user_id else None,
        "moldy_agent_id": str(agent_id) if agent_id else None,
        "moldy_agent_name": str(agent_name) if agent_name else None,
        "moldy_conversation_id": str(conversation_id) if conversation_id else None,
        "moldy_run_id": run_id,
        "moldy_model_id": str(model_id) if model_id else None,
        "moldy_checkpoint_id": str(checkpoint_id) if checkpoint_id else None,
        "moldy_route": (
            f"/agents/{agent_id}/conversations/{conversation_id}"
            if agent_id and conversation_id
            else None
        ),
        "moldy_source": source,
    }


def build_langfuse_run_context(
    cfg: Any,
    *,
    run_id: str | None,
    source: str,
) -> LangfuseRunContext:
    if not run_id or not _has_langfuse_credentials():
        return LangfuseRunContext(enabled=False)

    try:
        client = _get_langfuse_client()
        trace_id = _trace_id_for_run(client, run_id)
        callback = _build_callback_handler(trace_id=trace_id)
        trace = LangfuseTraceRecord(
            provider="langfuse",
            trace_id=trace_id,
            trace_url=_trace_url_for_id(client, trace_id),
        )
        tags = _tags_for_run(cfg, source=source)
        return LangfuseRunContext(
            enabled=True,
            trace=trace,
            callback=callback,
            client=client,
            metadata=_metadata_for_run(cfg, run_id=run_id, source=source),
            tags=tags,
            root_name=f"agent.{source}",
        )
    except Exception:
        logger.warning("Langfuse tracing disabled for this run", exc_info=True)
        return LangfuseRunContext(enabled=False)


def redact_payload(data: Any, **_kwargs: Any) -> Any:
    if not settings.langfuse_capture_input_output:
        if isinstance(data, dict):
            redacted = {}
            for key, value in data.items():
                if str(key).lower() in {"input", "output", "messages", "content", "prompt"}:
                    redacted[key] = "[redacted]"
                else:
                    redacted[key] = redact_payload(value)
            return redacted
        if isinstance(data, list):
            return [redact_payload(item) for item in data]
        if isinstance(data, str):
            return "[redacted]"
        return data

    if not settings.langfuse_redaction_enabled:
        return data

    if isinstance(data, dict):
        result: dict[Any, Any] = {}
        for key, value in data.items():
            key_l = str(key).lower()
            if any(secret in key_l for secret in _SECRET_VALUE_KEYS):
                result[key] = "[redacted]"
            else:
                result[key] = redact_payload(value)
        return result
    if isinstance(data, list):
        return [redact_payload(item) for item in data]
    if isinstance(data, str):
        masked = _TOKEN_RE.sub("[redacted]", data)
        return _HEX_SECRET_RE.sub("[redacted]", masked)
    return data


async def fetch_langfuse_observations(
    trace_id: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch observation rows for a trace.

    Returns ``([], reason)`` on every failure; callers should render the
    internal ``message_events`` fallback rather than failing the debug UI.
    """

    if not _has_langfuse_credentials():
        return [], "Langfuse disabled"

    base_url = _langfuse_base_url().rstrip("/")
    now = datetime.now(UTC)
    v2_params = {
        "traceId": trace_id,
        "fields": "core,basic,time,io,metadata,model,usage,trace_context",
        "fromStartTime": (now - timedelta(days=30)).isoformat(),
        "toStartTime": (now + timedelta(minutes=5)).isoformat(),
        "limit": "100",
    }
    auth = (settings.langfuse_public_key, settings.langfuse_secret_key)

    async with httpx.AsyncClient(timeout=8.0) as client:
        rows, error = await _fetch_langfuse_rows(
            client,
            f"{base_url}/api/public/v2/observations",
            auth=auth,
            params=v2_params,
            source="v2 observations",
            trace_id=trace_id,
        )
        if rows:
            return rows, None

        legacy_rows, legacy_error = await _fetch_langfuse_rows(
            client,
            f"{base_url}/api/public/traces/{trace_id}",
            auth=auth,
            params=None,
            source="legacy trace",
            trace_id=trace_id,
        )
        if legacy_rows:
            return legacy_rows, None

        legacy_observation_rows, legacy_observation_error = await _fetch_langfuse_rows(
            client,
            f"{base_url}/api/public/observations",
            auth=auth,
            params={"traceId": trace_id, "limit": "100"},
            source="legacy observations",
            trace_id=trace_id,
        )
        if legacy_observation_rows:
            return legacy_observation_rows, None

    errors = [
        item
        for item in (error, legacy_error, legacy_observation_error)
        if item and "returned no observations" not in item
    ]
    if errors:
        logger.warning("Langfuse observation fetch fallbacks failed: %s", "; ".join(errors))
        return [], "Langfuse observations unavailable; showing local trace events fallback"
    return [], "Langfuse trace returned no observations; showing local trace events fallback"


async def _fetch_langfuse_rows(
    client: httpx.AsyncClient,
    url: str,
    *,
    auth: tuple[str, str],
    params: dict[str, str] | None,
    source: str,
    trace_id: str,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        response = await client.get(url, params=params, auth=auth)
    except httpx.HTTPError:
        logger.warning("Langfuse %s fetch failed", source, exc_info=True)
        return [], f"Langfuse {source} unavailable"

    if not response.is_success:
        logger.warning("Langfuse %s fetch failed: HTTP %s", source, response.status_code)
        return [], f"Langfuse {source} unavailable (HTTP {response.status_code})"

    try:
        payload = response.json()
    except ValueError:
        logger.warning("Langfuse %s response was not JSON", source, exc_info=True)
        return [], f"Langfuse {source} response is not JSON"

    rows = _rows_from_langfuse_payload(payload, trace_id=trace_id)
    if not rows:
        return [], f"Langfuse {source} returned no observations"
    return rows, None


def _rows_from_langfuse_payload(payload: Any, *, trace_id: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            rows = data.get("observations") or data.get("items") or data.get("rows") or []
        else:
            rows = (
                data
                or payload.get("observations")
                or payload.get("items")
                or payload.get("rows")
                or []
            )
    else:
        rows = []

    if not isinstance(rows, list):
        return []

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "traceId" not in row and "trace_id" not in row:
            row = {**row, "traceId": trace_id}
        normalized.append(row)
    return normalized
