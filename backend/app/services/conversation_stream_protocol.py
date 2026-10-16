"""SSE protocol helpers and stream context extracted from ``conversation_stream_service``."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, NamedTuple

from fastapi.responses import StreamingResponse

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.streaming import StreamErrorRecord, format_sse
from app.database import TenantScoped
from app.observability.langfuse import LangfuseTraceRecord

logger = logging.getLogger("app.services.conversation_stream_service")


def stream_attr(name: str) -> Any:
    """Return ``conversation_stream_service.<name>`` as currently bound."""

    from app.services import conversation_stream_service

    return getattr(conversation_stream_service, name)


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def error_sse_pair(error_message: str) -> list[str]:
    return [
        format_sse(event_names.ERROR, {"message": error_message}),
        format_sse(event_names.MESSAGE_END, {"usage": {}, "content": ""}),
    ]


def sse_response(
    generator: AsyncGenerator[str, None],
    *,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    headers = dict(SSE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)


def sse_handler(
    executor_fn: Callable[[], AsyncGenerator[str, None]],
    *,
    log_msg: str,
    user_msg: str,
    on_complete: Callable[[bool], Awaitable[None]] | None = None,
    failure_probe: Callable[[], bool] | None = None,
    run_id: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    async def generate() -> AsyncGenerator[str, None]:
        success = False
        try:
            async for chunk in executor_fn():
                yield chunk
            failed = False
            if failure_probe is not None:
                try:
                    failed = failure_probe()
                except Exception:
                    logger.exception("stream failure probe failed (%s)", log_msg)
                    failed = True
            success = not failed
        except Exception:
            logger.exception(log_msg)
            for chunk in error_sse_pair(user_msg):
                yield chunk
        finally:
            if on_complete is not None:
                try:
                    await on_complete(success)
                except Exception:
                    logger.exception("on_complete hook failed (%s)", log_msg)

    extra: dict[str, str] = dict(extra_headers or {})
    if run_id:
        extra["X-Run-Id"] = run_id
    return sse_response(generate(), extra_headers=extra or None)


class StreamCtx(NamedTuple):
    run_id: str
    broker: EventBroker
    persist_cb: Callable[[list[dict[str, Any]]], Awaitable[None]]
    trace_sink: list[dict[str, Any]]
    msg_id_sink: list[str]
    error_sink: list[StreamErrorRecord]
    langfuse_sink: list[LangfuseTraceRecord]
    tenant: TenantScoped

    def as_stream_kwargs(self) -> dict[str, Any]:
        return {
            "trace_sink": self.trace_sink,
            "msg_id_sink": self.msg_id_sink,
            "error_sink": self.error_sink,
            "broker": self.broker,
            "persist_callback": self.persist_cb,
            "run_id": self.run_id,
            "langfuse_sink": self.langfuse_sink,
        }

    def has_stream_error(self) -> bool:
        return bool(self.error_sink)

    def finalize_callback(self, conversation_id: uuid.UUID) -> Callable[[bool], Awaitable[None]]:
        async def _cb(success: bool) -> None:
            await stream_attr("finalize_trace")(
                conversation_id,
                self.run_id,
                self.trace_sink,
                self.msg_id_sink,
                self.langfuse_sink,
                success=success,
                tenant=self.tenant,
            )

        return _cb
