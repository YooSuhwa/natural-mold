"""Per-run Langfuse trace context (split from ``langfuse``; re-exported there)."""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any

from app.observability.langfuse_redaction import capture_io_enabled, redact_payload

logger = logging.getLogger("app.observability.langfuse")


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
        if not capture_io_enabled():
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
