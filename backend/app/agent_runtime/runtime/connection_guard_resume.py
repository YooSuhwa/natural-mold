"""Asking (and replaying) the ``auth_required`` interrupt on resume (plan 11.5; G-03).

A resumed node runs again from the top with every answer recorded for it. The
ConnectionGuard used to re-read the connection first and only then call
``interrupt()``, so a connection repaired between the ask and the answer silently
dropped a ``skip``/``cancel``, and a later answer (``cancel`` after a still-broken
``retry``) lost to the earlier one. ``ask`` therefore replays every recorded answer
and returns the latest; ``recorded_answers`` tells the guard an answer is waiting even
when nothing is broken any more.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.config import get_config
from langgraph.types import interrupt

from app.agent_runtime.runtime.connection_guard_items import AuthDecision, decision_of

logger = logging.getLogger(__name__)

try:  # private LangGraph key (pinned 1.2.x; canary: test_g03_scratchpad_canary.py)
    from langgraph._internal._constants import CONFIG_KEY_SCRATCHPAD
except ImportError:  # pragma: no cover - a LangGraph upgrade moved it
    CONFIG_KEY_SCRATCHPAD = None


def recorded_answers() -> int:
    """How many answers this node execution was resumed with; read before any ask.

    ``get_null_resume(False)`` keeps returning a consumed value, so the count must be
    taken while nothing has been asked yet in this execution.
    """

    if CONFIG_KEY_SCRATCHPAD is None:
        return 0  # fallback: the first answer only (pre-G-03 behaviour)
    try:
        scratchpad = get_config().get("configurable", {})[CONFIG_KEY_SCRATCHPAD]
        return len(scratchpad.resume) + (scratchpad.get_null_resume(False) is not None)
    except RuntimeError:  # outside a graph run
        return 0
    except Exception:  # noqa: BLE001 - a changed scratchpad shape must not break the run
        logger.warning("connection_guard: resume scratchpad unreadable", exc_info=True)
        return 0


def ask(payload: dict[str, Any], asked: int, recorded: int) -> tuple[AuthDecision | None, int]:
    """Ask attempt ``asked + 1`` (raises when unanswered); the latest answer wins."""

    asked += 1
    decision = decision_of(interrupt({**payload, "attempt": asked}))
    while asked < recorded:
        asked += 1
        decision = decision_of(interrupt({**payload, "attempt": asked}))
    return decision, asked


__all__ = ["ask", "recorded_answers"]
