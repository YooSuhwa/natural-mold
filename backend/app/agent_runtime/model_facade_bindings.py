"""Call-time lookups of the legacy ``model_factory`` compatibility facade.

Tests patch ``model_factory.create_chat_model`` / ``model_factory.ChatOpenAI``
after import; extracted model modules read them here at call time.
"""

from __future__ import annotations

from typing import Any


def model_attr(name: str) -> Any:
    """Return ``model_factory.<name>`` as currently bound."""

    from app.agent_runtime import model_factory

    return getattr(model_factory, name)
