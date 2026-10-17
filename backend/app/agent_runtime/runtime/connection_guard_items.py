"""Typed refusals and the bundled ``auth_required`` interrupt (plan 11.4, 11.5; F-07).

``ConnectionGuardMiddleware`` answers a blocked tool call with an error ToolMessage
that keeps the call's ``tool_call_id`` (so the ToolNode never runs it) and collects
every connection problem of one model step into ONE 11.5 interrupt payload:

    {"type": "auth_required", "reason": ..., "items": [...], "allowed_decisions": ["respond"]}

Items of the same connection are merged (``tool_names`` unioned). The resume answer is
the existing ``Decision`` (``{"type": "respond", "message": "retry"|"skip"|"cancel"}``),
bare or inside the HITL ``{"decisions": [...]}`` envelope. ``fixable_by=owner`` items are
never asked of the runner: the owner is notified instead (log record until H-02 ships
notifications). Nothing here carries a secret value.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.messages.tool import ToolCall

logger = logging.getLogger(__name__)

ARTIFACT_KEY = "connection_guard"
MCP_AUTH_ARTIFACT_KEY = "mcp_auth_error"  # F-12 ``mcp_auth_context.ARTIFACT_KEY``
BINDING_REQUIRED = "credential_binding_required"
AuthDecision = Literal["retry", "skip", "cancel"]
_DECISIONS: tuple[AuthDecision, ...] = ("retry", "skip", "cancel")
_ITEM_KEY_FIELDS = ("resource_type", "resource_name", "credential_id", "fixable_by", "connect")

DETAILS = {
    "permission_required": "this run is not allowed to use this tool",
    "auth_required": "the connection this tool needs is not usable; it must be reconnected",
    "tool_not_allowed": "this tool is not part of the agent's authorized tools",
    "authz_unavailable": "permission could not be checked; try again later",
    "owner_reconnect_required": "the connection must be repaired by its owner (notified)",
    "skipped": "skipped by the user",
    "cancelled": "the run was cancelled by the user",
    "reconnected": "the connection was updated; call the tool again if it is still needed",
}


class ConnectionBlockedError(RuntimeError):
    """A run nobody can answer (trigger, invoke API) needs a connection: it ends blocked.

    ``code`` is the run's recorded reason (``run_error_codes``); ``payload`` is the
    11.5 interrupt the run would have raised (no secrets).
    """

    def __init__(self, payload: Mapping[str, Any]) -> None:
        super().__init__("auth_required")
        self.code = "auth_required"
        self.payload = dict(payload)


def refusal(call: ToolCall, code: str) -> ToolMessage:
    """Error ToolMessage for ``call`` (same ``tool_call_id``) with a typed artifact."""

    name = call["name"]
    return ToolMessage(
        f"[Connection Guard] {name}: {code}: {DETAILS.get(code, code)}",
        tool_call_id=call["id"] or "",
        name=name,
        status="error",
        artifact={ARTIFACT_KEY: {"code": code}},
    )


def resolved(message: ToolMessage, code: str) -> ToolMessage:
    """Replace an answered auth-error ToolMessage (same message id) after the user decided."""

    artifact = dict(message.artifact) if isinstance(message.artifact, Mapping) else {}
    auth = artifact.get(MCP_AUTH_ARTIFACT_KEY)
    if isinstance(auth, Mapping):
        artifact[MCP_AUTH_ARTIFACT_KEY] = {**auth, "interrupt": None}
    artifact[ARTIFACT_KEY] = {"code": code}
    name = message.name or "tool"
    content = f"[Connection Guard] {name}: {code}: {DETAILS[code]}"
    return message.model_copy(update={"content": content, "artifact": artifact})


def bundle(problems: Iterable[tuple[str, Mapping[str, Any]]]) -> dict[str, Any] | None:
    """One 11.5 payload for ``(reason, item)`` pairs; ``None`` when there is nothing."""

    merged: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    for reason, item in problems:
        key = json.dumps({k: item.get(k) for k in _ITEM_KEY_FIELDS}, sort_keys=True, default=str)
        entry = merged.get(key)
        names = [str(name) for name in item.get("tool_names") or ()]
        if entry is None:
            merged[key] = {**item, "tool_names": list(dict.fromkeys(names))}
        else:
            entry["tool_names"] += [name for name in names if name not in entry["tool_names"]]
        if reason not in reasons:
            reasons.append(reason)
    if not merged:
        return None
    return {
        "type": "auth_required",
        "reason": reasons[0] if len(reasons) == 1 else "auth_needed",
        "items": list(merged.values()),
        "allowed_decisions": ["respond"],
    }


def decision_of(response: object) -> AuthDecision | None:
    """``retry``/``skip``/``cancel`` from a resume value; ``None`` asks again."""

    message: object = response
    if isinstance(response, Mapping):
        decisions = response.get("decisions")
        candidates = decisions if isinstance(decisions, list) else [response]
        message = next(
            (
                item.get("message")
                for item in candidates
                if isinstance(item, Mapping) and item.get("type") == "respond"
            ),
            None,
        )
    for decision in _DECISIONS:
        if message == decision:
            return decision
    return None


def auth_interrupt_of(message: ToolMessage) -> dict[str, Any] | None:
    """The 11.5 payload an executed call asked for (F-12 artifact or a binding error)."""

    artifact = message.artifact if isinstance(message.artifact, Mapping) else {}
    auth = artifact.get(MCP_AUTH_ARTIFACT_KEY)
    if isinstance(auth, Mapping) and isinstance(auth.get("interrupt"), Mapping):
        return dict(auth["interrupt"])
    if isinstance(message.content, str) and message.content.startswith(BINDING_REQUIRED):
        item = {
            "resource_type": "tool",
            "resource_name": message.name or "tool",
            "tool_names": [message.name or "tool"],
            "credential_id": None,
            "fixable_by": "me",
            "connect": {"kind": "binding"},
        }
        return {"type": "auth_required", "reason": "auth_needed", "items": [item]}
    return None


_NOTIFIED: OrderedDict[tuple[str, str], None] = OrderedDict()
_NOTIFIED_LIMIT = 4096


def notify_owner(items: Iterable[Mapping[str, Any]], *, run: str) -> None:
    """Owner notification for ``fixable_by=owner`` connections (11.5), once per
    (run, credential) — a resumed node re-runs its hooks.

    H-02 (notifications) is not built yet; the request is a structured log record
    carrying only names and ids.
    """

    for item in items:
        key = (run, str(item.get("credential_id") or item.get("resource_name")))
        if key in _NOTIFIED:
            continue
        _NOTIFIED[key] = None
        while len(_NOTIFIED) > _NOTIFIED_LIMIT:
            _NOTIFIED.popitem(last=False)
        logger.info(
            "connection_guard owner_notification resource_type=%s resource_name=%s "
            "credential_id=%s tools=%s",
            item.get("resource_type"),
            item.get("resource_name"),
            item.get("credential_id"),
            ",".join(str(name) for name in item.get("tool_names") or ()),
        )


__all__ = [
    "ARTIFACT_KEY",
    "AuthDecision",
    "ConnectionBlockedError",
    "auth_interrupt_of",
    "bundle",
    "decision_of",
    "notify_owner",
    "refusal",
    "resolved",
]
