"""Call-time lookups of the legacy stream/invoke runner facades.

Tests patch ``agent_stream_runner`` / ``langgraph_agent_stream_runner``
attributes (``_prepare_agent``, ``hooks``, ``stream_agent_response`` ...)
after import and after a prior call, so extracted runner bodies resolve those
collaborators through the facade module at call time.
"""

from __future__ import annotations

from typing import Any


def stream_attr(name: str) -> Any:
    """Return ``agent_stream_runner.<name>`` as currently bound."""

    from app.agent_runtime import agent_stream_runner

    return getattr(agent_stream_runner, name)


def langgraph_attr(name: str) -> Any:
    """Return ``langgraph_agent_stream_runner.<name>`` as currently bound."""

    from app.agent_runtime import langgraph_agent_stream_runner

    return getattr(langgraph_agent_stream_runner, name)
