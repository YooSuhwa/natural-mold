"""Persist the ordinary chat root's evicted checkpoint messages before compaction."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, ClassVar

from deepagents.backends import BackendProtocol
from deepagents.middleware.summarization import SUMMARIZATION_EVENT_KEY, SummarizationMiddleware
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage

from app.agent_runtime.offload_storage import ScopedOffloadBackend
from app.agent_runtime.offload_storage_types import ARCHIVE_FILE, rollback_session_id


@dataclass(slots=True)
class _ArchiveBuild:
    backend: ScopedOffloadBackend
    writes_enabled: bool | None


_archive_build: ContextVar[_ArchiveBuild | None] = ContextVar(
    "moldy_chat_archive_build", default=None
)


@contextmanager
def chat_archive_build(backend: ScopedOffloadBackend, *, writes_enabled: bool) -> Iterator[None]:
    """Select the root's compaction format only while its synchronous graph is built."""
    build = _ArchiveBuild(backend, writes_enabled)
    token = _archive_build.set(build)
    try:
        yield
    finally:
        build.writes_enabled = None
        _archive_build.reset(token)


def chat_archive_writes_for(backend: object) -> bool | None:
    build = _archive_build.get()
    return build.writes_enabled if build is not None and build.backend is backend else None


@dataclass(slots=True)
class _Compaction:
    request: ModelRequest
    history_path: str | None = None

    @property
    def checkpoint_id(self) -> str:
        info = self.request.runtime.execution_info
        if info is None or not info.checkpoint_id:
            raise RuntimeError("chat_archive_checkpoint_required")
        if "|" in info.checkpoint_ns:
            raise RuntimeError("chat_archive_root_required")
        return info.checkpoint_id

    @property
    def previous_archive(self) -> str | None:
        event = self.request.state.get(SUMMARIZATION_EVENT_KEY)
        path = event.get("file_path") if isinstance(event, dict) else None
        if isinstance(path, str) and ARCHIVE_FILE.fullmatch(path.rsplit("/", 1)[-1]):
            return path
        return None


# A graph can serve concurrent model calls. Never keep request state on the middleware.
_compaction: ContextVar[_Compaction] = ContextVar("moldy_chat_archive_compaction")


def _scoped_backend(backend: BackendProtocol) -> ScopedOffloadBackend:
    if not isinstance(backend, ScopedOffloadBackend):
        raise RuntimeError("chat_archive_owner_required")
    return backend


def _archive(backend: BackendProtocol, selected_count: int) -> str:
    compaction = _compaction.get()
    state = compaction.request.state
    # Upstream's cutoff is an absolute index into the checkpoint. Use its own ID-aware
    # translation for forks. Ignore request-only tool-arg, attachment and media rewrites.
    originals = state["messages"]
    event = state.get(SUMMARIZATION_EVENT_KEY)
    to_state = SummarizationMiddleware._compute_state_cutoff  # noqa: SLF001
    # On a switch from single-file history retain the checkpoint's *full* canonical
    # prefix. A mutable Markdown snapshot cannot recover message fields or fork ownership.
    prior = compaction.previous_archive
    start = to_state(event, 1, originals) if prior else 0
    end = to_state(event, selected_count, originals)
    if not 0 <= start < end <= len(originals):
        raise RuntimeError("chat_archive_invalid_state_cutoff")
    return _scoped_backend(backend).archive_compaction(
        compaction.checkpoint_id, originals[start:end], previous_file_path=prior
    )


class ArchiveSummarizationMiddleware(SummarizationMiddleware):
    """Replace the Deep Agents 0.7.42 markdown append while keeping its policy, model and media.

    Its media step runs after selection and before either the summary model or the legacy
    history append. A failed durable write cannot advance the checkpoint's cutoff.
    """

    writes_archives: ClassVar[bool] = True

    @property
    def name(self) -> str:
        # Replace exactly the installed summarizer slot, including in the guardrail stack.
        return "SummarizationMiddleware"

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse | ExtendedModelResponse:
        handle = _compaction.set(_Compaction(request))
        try:
            return super().wrap_model_call(request, handler)
        finally:
            _compaction.reset(handle)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | ExtendedModelResponse:
        handle = _compaction.set(_Compaction(request))
        try:
            return await super().awrap_model_call(request, handler)
        finally:
            _compaction.reset(handle)

    def _get_session_id(self, state: Mapping[str, Any]) -> str:
        ctx = _compaction.get()
        if not self.writes_archives and (prior := ctx.previous_archive):
            return rollback_session_id(prior, ctx.checkpoint_id)
        return super()._get_session_id(state)

    def _restore_legacy(self, backend: BackendProtocol, messages: list[AnyMessage]) -> None:
        ctx = _compaction.get()
        if not self.writes_archives and (prior := ctx.previous_archive):
            ctx.history_path = _scoped_backend(backend).archive_checkpoint_to_legacy(
                prior,
                self._get_session_id(ctx.request.state),
                self._filter_summary_messages(messages),
            )

    def _offload_inline_media(
        self, backend: BackendProtocol, messages: list[AnyMessage]
    ) -> tuple[list[AnyMessage], int]:
        if self.writes_archives:
            _compaction.get().history_path = _archive(backend, len(messages))
        rewritten, failures = super()._offload_inline_media(backend, messages)
        self._restore_legacy(backend, rewritten)
        return rewritten, failures

    async def _aoffload_inline_media(
        self, backend: BackendProtocol, messages: list[AnyMessage]
    ) -> tuple[list[AnyMessage], int]:
        if self.writes_archives:
            _compaction.get().history_path = await asyncio.to_thread(
                _archive, backend, len(messages)
            )
        rewritten, failures = await super()._aoffload_inline_media(backend, messages)
        if not self.writes_archives and _compaction.get().previous_archive:
            await asyncio.to_thread(self._restore_legacy, backend, rewritten)
        return rewritten, failures

    def _offload_to_backend(
        self, backend: BackendProtocol, messages: list[AnyMessage], session_id: str
    ) -> str | None:
        return _compaction.get().history_path or super()._offload_to_backend(
            backend, messages, session_id
        )

    async def _aoffload_to_backend(
        self, backend: BackendProtocol, messages: list[AnyMessage], session_id: str
    ) -> str | None:
        return _compaction.get().history_path or await super()._aoffload_to_backend(
            backend, messages, session_id
        )


class LegacyArchiveSummarizationMiddleware(ArchiveSummarizationMiddleware):
    """Write standard Markdown, preserving a checkpoint from the chunked format on rollback."""

    writes_archives = False
