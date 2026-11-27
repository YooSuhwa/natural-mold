"""Translate an AG-UI interrupt answer using the action and policy at its checkpoint."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.exceptions import ConflictError, ValidationError
from app.routers.conversation_agent_protocol_resume_redaction import (
    RedactedResumeArgsUnavailable,
    _restore_redacted_response,
)
from app.routers.conversation_resume_decisions import INVALID, STALE, decision_problem

if TYPE_CHECKING:
    from ag_ui.core import ResumeEntry


def _invalid(message: str) -> ValidationError:
    return ValidationError(INVALID, message)


def _decision_envelope(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"decisions"}:
        raise _invalid("Expected an object containing decisions")
    decisions = value["decisions"]
    if not isinstance(decisions, list) or not all(isinstance(item, dict) for item in decisions):
        raise _invalid("Expected a list of decisions")
    return [dict(item) for item in decisions]


def _tool_actions(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = value.get("action_requests")
    if (
        not isinstance(actions, list)
        or not actions
        or not isinstance(value.get("review_configs"), list)
    ):
        raise _invalid("The interrupt has no actionable review configuration")
    if not all(
        isinstance(action, dict)
        and isinstance(action.get("name"), str)
        and action["name"]
        and isinstance(action.get("args"), dict)
        for action in actions
    ):
        raise _invalid("The pending action cannot be read")
    return actions


def _validate_tool_answer(decision: dict[str, Any], action: dict[str, Any], *, config: Any) -> None:
    fields = {
        "approve": {"type", "scope"},
        "reject": {"type", "message"},
        "respond": {"type", "message"},
        "edit": {"type", "edited_action"},
    }
    kind = decision.get("type")
    if not isinstance(kind, str) or kind not in fields or set(decision) - fields[kind]:
        raise _invalid("The decision contains unsupported fields")
    if "message" in decision and not isinstance(decision["message"], str):
        raise _invalid("A decision message must be text")
    if "scope" in decision:
        from app.agent_runtime.skill_builder.tools import SESSION_CONSENT_ELIGIBLE_TOOLS

        if (
            decision["scope"] != "session"
            or action["name"] not in SESSION_CONSENT_ELIGIBLE_TOOLS
            or not isinstance(config, Mapping)
            or config.get("action_name") != action["name"]
            or config.get("session_consent_eligible") is not True
        ):
            raise _invalid("This action cannot grant session consent")
    if kind == "edit":
        edited = decision.get("edited_action")
        if (
            not isinstance(edited, dict)
            or set(edited) - {"name", "args"}
            or not isinstance(edited.get("args"), dict)
            or edited.get("name", action["name"]) != action["name"]
        ):
            raise _invalid("An edit may only replace arguments of its pending action")


def checkpoint_response(
    entry: ResumeEntry, *, value: Any, native_kind: str | None
) -> dict[str, Any]:
    """Return a LangGraph answer for exactly this server-issued pending slot."""
    if not isinstance(value, Mapping):
        raise _invalid("The interrupt cannot be answered by a chat client")
    cancelled = entry.status == "cancelled"
    if cancelled and "payload" in entry.model_fields_set:
        raise _invalid("A cancelled interrupt cannot contain a payload")
    decisions = [] if cancelled else _decision_envelope(entry.payload)
    actions: list[dict[str, Any]] = []
    kind = native_kind or value.get("type")
    if kind == "auth_required":
        attempt = value.get("attempt", 1)
        if type(attempt) is not int or attempt < 1:
            raise ConflictError(STALE, "The authentication interrupt has no current attempt")
        allowed = value.get("allowed_decisions", ["respond"])
        if not isinstance(allowed, list) or "respond" not in allowed:
            raise _invalid("This authentication interrupt cannot accept a response")
        if cancelled:
            decisions = [{"type": "respond", "message": "cancel", "attempt": attempt}]
        if len(decisions) != 1 or set(decisions[0]) != {"type", "message", "attempt"}:
            raise _invalid("An authentication response must identify the current attempt")
        if decisions[0]["type"] != "respond" or not isinstance(decisions[0]["message"], str):
            raise _invalid("An authentication response requires a text decision")
        if type(decisions[0]["attempt"]) is not int or decisions[0]["attempt"] != attempt:
            raise ConflictError(STALE, "The authentication attempt has changed")
    elif kind == "ask_user":
        if cancelled:
            decisions = [{"type": "respond", "message": ""}]
        if (
            len(decisions) != 1
            or set(decisions[0]) != {"type", "message"}
            or decisions[0]["type"] != "respond"
            or not isinstance(decisions[0]["message"], str)
        ):
            raise _invalid("A question requires one text response")
    elif kind in (None, "approval") and "action_requests" in value:
        actions = _tool_actions(value)
        if cancelled:
            decisions = [{"type": "reject"} for _ in actions]
        if len(decisions) != len(actions):
            raise _invalid("Each pending action requires its own decision")
        configs = value["review_configs"]
        for index, (decision, action) in enumerate(zip(decisions, actions, strict=True)):
            config = configs[index] if index < len(configs) else None
            _validate_tool_answer(decision, action, config=config)
    else:
        raise _invalid("The interrupt cannot be answered by a chat client")
    response = {"decisions": decisions}
    if problem := decision_problem(response, value):
        code, message, status = problem
        if status == 409:
            raise ConflictError(code, message)
        raise ValidationError(code, message)
    try:
        return _restore_redacted_response(response, actions)
    except RedactedResumeArgsUnavailable as exc:
        raise _invalid("An edited field must be replaced explicitly") from exc
