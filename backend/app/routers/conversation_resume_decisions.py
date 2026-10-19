"""Server-side check of resume decisions (plan 11.5; G-06).

Before a resume touches the graph, every submitted answer is compared with the
pending interrupt it answers:

* ``auth_required`` (F-07 ConnectionGuard): one ``respond`` decision (bare or in the
  ``{"decisions": [...]}`` envelope) whose ``message`` is ``retry``/``skip``/``cancel``;
  an optional ``attempt`` must be the pending re-ask (a stale card answering an older
  attempt is refused).
* HITL approval (``action_requests``/``review_configs``): exactly one decision per
  action, each type inside that action's ``allowed_decisions`` (e.g. ``risk.py``:
  ``execute_in_skill`` = approve/reject), ``edit`` with ``edited_action.args``. The
  ``edited_action.name`` stays optional — the backend fills it by index (edit-by-index
  contract) — and ``scope: "session"`` is the AD-4 consent extension.

Anything else is a typed 4xx (``INVALID_RESUME_DECISION`` 422, ``STALE_INTERRUPT`` 409).
``require_allowed_rest_resume`` (interrupts module) applies it to REST resumes.
Only these two payload shapes are checked. ``ask_user`` reaches here as an approval
payload when the HITL middleware wraps the tool (then it is checked like any approval:
``respond`` only); the tool's own native ``{"type": "ask_user"}`` payload and other
custom interrupts pass unchecked. An approval payload that cannot be read fails closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

INVALID = "INVALID_RESUME_DECISION"
STALE = "STALE_INTERRUPT"
AUTH_RESPONSES = ("retry", "skip", "cancel")
_AUTH_KEYS = frozenset({"type", "message", "attempt"})
_HITL_KEYS = frozenset({"type", "edited_action", "message", "scope"})
DecisionProblem = tuple[str, str, int]  # code, message, HTTP status


def _invalid(message: str) -> DecisionProblem:
    return INVALID, message, 422


def _envelope(response: Any) -> list[Any] | None:
    if isinstance(response, Mapping) and isinstance(response.get("decisions"), list):
        return list(response["decisions"])
    return None


def is_auth_interrupt(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("type") == "auth_required"


def _attempt(value: Mapping[str, Any]) -> int:
    attempt = value.get("attempt")
    return attempt if type(attempt) is int else 1


def _auth_problem(response: Any, pending: Mapping[str, Any]) -> DecisionProblem | None:
    decisions = _envelope(response)
    decisions = [response] if decisions is None else decisions
    if len(decisions) != 1:
        return _invalid("auth_required takes exactly one decision")
    decision = decisions[0]
    if not isinstance(decision, Mapping) or set(decision) - _AUTH_KEYS:
        return _invalid("auth_required decision has unsupported fields")
    allowed = pending.get("allowed_decisions") or ["respond"]
    if decision.get("type") not in allowed:
        return _invalid(f"decision type is not allowed (allowed: {list(allowed)})")
    if decision.get("message") not in AUTH_RESPONSES:
        return _invalid("auth_required message must be retry, skip or cancel")
    if "attempt" in decision and (
        type(decision["attempt"]) is not int or decision["attempt"] != _attempt(pending)
    ):
        return STALE, "auth_required attempt is not the pending attempt", 409
    return None


def _allowed(action: Any, configs: Sequence[Any], index: int) -> frozenset[str]:
    name = action.get("name") if isinstance(action, Mapping) else None
    config = configs[index] if index < len(configs) else None
    if not isinstance(config, Mapping) or config.get("action_name") != name:
        config = next(
            (c for c in configs if isinstance(c, Mapping) and c.get("action_name") == name), None
        )
    allowed = config.get("allowed_decisions") if isinstance(config, Mapping) else None
    if not isinstance(allowed, list):
        return frozenset()  # fail closed: nothing is allowed for an unreadable config
    return frozenset(item for item in allowed if isinstance(item, str))


def _decision_problem(index: int, decision: Any, allowed: frozenset[str]) -> str | None:
    if not isinstance(decision, Mapping) or set(decision) - _HITL_KEYS:
        return f"decision {index} has unsupported fields"
    kind = decision.get("type")
    if kind not in allowed:
        return f"decision {index}: {kind!r} is not allowed (allowed: {sorted(allowed)})"
    message = decision.get("message")
    if kind == "respond" and not isinstance(message, str):
        return f"decision {index}: respond requires a message"
    if message is not None and not isinstance(message, str):
        return f"decision {index}: message must be a string"
    edited = decision.get("edited_action")
    if kind == "edit" and not (
        isinstance(edited, Mapping)
        and isinstance(edited.get("args"), Mapping)
        and isinstance(edited.get("name", ""), str)
    ):
        return f"decision {index}: edit requires edited_action.args"
    if "scope" in decision and (decision["scope"] != "session" or kind != "approve"):
        return f"decision {index}: scope is only 'session' on approve"
    return None


def _hitl_problem(response: Any, pending: Mapping[str, Any]) -> DecisionProblem | None:
    actions, configs = pending.get("action_requests"), pending.get("review_configs")
    if not isinstance(actions, list) or not isinstance(configs, list):
        return _invalid("the pending approval cannot be read")
    decisions = _envelope(response)
    if decisions is None:
        return _invalid("approval interrupts take {'decisions': [...]}")
    if len(decisions) != len(actions):
        return _invalid(f"expected {len(actions)} decisions, got {len(decisions)}")
    for index, decision in enumerate(decisions):
        problem = _decision_problem(index, decision, _allowed(actions[index], configs, index))
        if problem is not None:
            return _invalid(problem)
    return None


def decision_problem(response: Any, pending_value: Any) -> DecisionProblem | None:
    """Why ``response`` is not an allowed answer to ``pending_value`` (``None``: allowed)."""

    if is_auth_interrupt(pending_value):
        return _auth_problem(response, pending_value)
    if (
        isinstance(pending_value, Mapping)
        and "action_requests" in pending_value
        and "review_configs" in pending_value
    ):
        return _hitl_problem(response, pending_value)
    return None


def auth_decision_record(
    interrupt_id: str, response: Any, pending_value: Any
) -> dict[str, Any] | None:
    """The persisted answer to an ``auth_required`` interrupt (run metadata), or ``None``."""

    if not is_auth_interrupt(pending_value):
        return None
    decisions = _envelope(response) or [response]
    decision = decisions[0] if isinstance(decisions[0], Mapping) else {}
    return {
        "interrupt_id": interrupt_id,
        "kind": "auth_required",
        "decision": decision.get("message"),
        "attempt": _attempt(pending_value),
    }


__all__ = [
    "AUTH_RESPONSES",
    "DecisionProblem",
    "auth_decision_record",
    "decision_problem",
    "is_auth_interrupt",
]
