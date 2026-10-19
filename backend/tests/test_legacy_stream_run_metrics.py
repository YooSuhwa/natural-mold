"""The run worker passes ``run_metrics`` to every executor (legacy runtime too)."""

from __future__ import annotations

import inspect

from app.agent_runtime.agent_stream_runner import execute_agent_stream, resume_agent_stream


def test_legacy_executors_accept_run_metrics() -> None:
    for executor in (execute_agent_stream, resume_agent_stream):
        parameter = inspect.signature(executor).parameters.get("run_metrics")
        assert parameter is not None, executor.__name__
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None
