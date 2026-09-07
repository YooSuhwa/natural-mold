"""LangChain callback wiring for content-free model generation timing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler, BaseCallbackManager

from app.agent_runtime.run_metrics import RunMetricsAccumulator


class RunMetricsCallback(BaseCallbackHandler):
    """Forward only model lifecycle IDs; never retain prompts, outputs, or errors."""

    def __init__(self, accumulator: RunMetricsAccumulator) -> None:
        self._accumulator = accumulator

    def on_chat_model_start(
        self,
        _serialized: dict[str, Any],
        _messages: list[list[Any]],
        *,
        run_id: UUID,
        **_kwargs: Any,
    ) -> None:
        self._accumulator.start_model_generation(str(run_id))

    def on_llm_start(
        self,
        _serialized: dict[str, Any],
        _prompts: list[str],
        *,
        run_id: UUID,
        **_kwargs: Any,
    ) -> None:
        self._accumulator.start_model_generation(str(run_id))

    def on_llm_end(self, _response: Any, *, run_id: UUID, **_kwargs: Any) -> None:
        self._accumulator.finish_model_generation(str(run_id))

    def on_llm_error(self, _error: BaseException, *, run_id: UUID, **_kwargs: Any) -> None:
        self._accumulator.finish_model_generation(str(run_id))


def configure_run_metrics_callback(
    config: Mapping[str, Any],
    accumulator: RunMetricsAccumulator,
) -> dict[str, Any]:
    """Return a config copy with metrics timing added after existing callbacks."""
    configured = dict(config)
    callback = RunMetricsCallback(accumulator)
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        manager = callbacks.copy()
        manager.add_handler(callback, inherit=True)
        configured["callbacks"] = manager
    elif isinstance(callbacks, list):
        configured["callbacks"] = [*callbacks, callback]
    else:
        configured["callbacks"] = [callback]
    return configured
