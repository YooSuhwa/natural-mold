"""Legacy messages-API rebuild of a pending ``auth_required`` card (plan 13.6, G-05).

The legacy runtime renders pending interrupts from synthetic tool calls on the persisted
assistant message (``interrupts._hydrate_pending_interrupt_tool_calls``). The F-07
ConnectionGuard interrupt is not a HITL request, so it gets its own card: the
``moldy_auth_required`` tool call in the exact shape the frontend builds live
(``standard-interrupt.ts``), keyed by interrupt id + ``attempt``.

Still pending means: the target is the latest assistant message (a resumed run adds a
new one) and none of its asked calls carries a resolved guard answer (``skipped`` /
``cancelled`` / ``reconnected`` — what ``connection_guard_items.resolved``/``refusal``
write when the user answered).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.schemas.conversation import MessageResponse

AUTH_ACTION = "moldy_auth_required"
_RESOLVED = re.compile(r"^\[Connection Guard\] [^:]+: (?:skipped|cancelled|reconnected):")


def is_auth_payload(data: Mapping[str, Any] | None) -> bool:
    return isinstance(data, Mapping) and data.get("type") == "auth_required"


def _attempt(payload: Mapping[str, Any]) -> int:
    attempt = payload.get("attempt")
    return attempt if type(attempt) is int else 1


def auth_tool_call(payload: Mapping[str, Any]) -> dict[str, Any]:
    interrupt_id = payload.get("interrupt_id")
    interrupt_id = interrupt_id if isinstance(interrupt_id, str) else ""
    attempt = _attempt(payload)
    approval_id = f"{interrupt_id}:0"
    items = payload.get("items")
    return {
        "id": f"{approval_id}#{attempt}",
        "name": AUTH_ACTION,
        "args": {
            "reason": payload.get("reason") if isinstance(payload.get("reason"), str) else "",
            "items": items if isinstance(items, list) else [],
            "attempt": attempt,
            "approval_id": approval_id,
            "allowed_decisions": ["respond"],
            "hitl_interrupt_id": interrupt_id,
            "hitl_action_index": 0,
            "hitl_total_actions": 1,
        },
    }


def _asked_tool_names(payload: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in payload.get("items") or ():
        if isinstance(item, Mapping):
            names.update(name for name in item.get("tool_names") or () if isinstance(name, str))
    return names


def _asked_call_ids(response: MessageResponse, names: set[str]) -> set[str]:
    return {
        str(call.get("id"))
        for call in response.tool_calls or []
        if isinstance(call, Mapping) and call.get("name") in names and call.get("id")
    }


def _is_pending(
    response: MessageResponse,
    call_ids: set[str],
    responses: Sequence[MessageResponse],
) -> bool:
    latest_assistant = next((r for r in reversed(responses) if r.role == "assistant"), None)
    if latest_assistant is not response:
        return False
    return not any(
        r.role == "tool" and r.tool_call_id in call_ids and _RESOLVED.match(r.content or "")
        for r in responses
    )


def hydrate_auth_cards(
    pending: Mapping[str, Mapping[str, Any]],
    response_by_id: Mapping[str, MessageResponse],
    responses: Sequence[MessageResponse],
) -> None:
    """Append the card for each ``{response id: latest auth payload}`` still pending."""

    for response_id, payload in pending.items():
        response = response_by_id.get(response_id)
        if response is None:
            continue
        call_ids = _asked_call_ids(response, _asked_tool_names(payload))
        if not call_ids or not _is_pending(response, call_ids, responses):
            continue
        card = auth_tool_call(payload)
        calls = [call for call in response.tool_calls or [] if call.get("name") != AUTH_ACTION]
        response.tool_calls = [*calls, card]
