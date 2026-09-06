"""Content-free baseline extraction for pre-input checkpoint messages."""

from __future__ import annotations

from collections.abc import Iterable

from langchain_core.messages import BaseMessage

from app.agent_runtime.run_metrics_types import MessageIdentity


def baseline_message_identities(
    messages: Iterable[BaseMessage],
) -> frozenset[MessageIdentity]:
    """Return root assistant identities without retaining checkpoint content."""
    identities: set[MessageIdentity] = set()
    for message in messages:
        if message.type != "ai" or not isinstance(message.id, str) or not message.id:
            continue
        identities.add(((), message.id))
    return frozenset(identities)
