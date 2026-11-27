"""Public identities and lifetimes for nested work within one AG-UI stream."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ag_ui.core import BaseEvent, SubagentErrorEvent, SubagentFinishedEvent, SubagentStartedEvent
from ag_ui.core.events import SubagentFinishedSuspendedOutcome

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.tools.keys import DELEGATION_TOOL

Namespace = tuple[str, ...]


def public_subagent_id(thread_id: str, namespace: Namespace) -> str:
    """A checkpoint namespace identifies an invocation, including across a resume."""
    if not namespace:
        raise ValueError("the parent has no subagent id")
    identity = json.dumps([thread_id, *namespace], ensure_ascii=False, separators=(",", ":"))
    return "child-" + hashlib.sha256(identity.encode()).hexdigest()


def journal_namespace(value: object) -> Namespace | None:
    if not isinstance(value, (list, tuple)) or not all(isinstance(s, str) and s for s in value):
        return None
    return tuple(value)


def public_journal_namespace(event: StoredProtocolEvent) -> Namespace | None:
    namespace = journal_namespace(event["namespace"])
    if namespace is None:
        return None
    method = event["method"]
    data = event["data"]
    if method not in {"lifecycle", "subagents", "subgraphs"}:
        return namespace
    if not isinstance(data, Mapping):
        return None
    # Some versions deliver child lifecycle on the root envelope. If more
    # than one non-empty declaration exists they must identify the same scope.
    for field in ("namespace", "path"):
        if field in data:
            declared = journal_namespace(data[field])
            if declared is None or (namespace and declared != namespace):
                return None
            namespace = declared
    if method != "lifecycle" and not namespace:
        return None
    return namespace


def parent_call(data: Mapping[str, Any]) -> str | None:
    cause = data.get("cause")
    if isinstance(cause, Mapping) and cause.get("type") == "toolCall":
        call = cause.get("tool_call_id")
    else:
        call = data.get("tool_call_id")
    # trigger_call_id is a graph invocation ID, not the parent's tool call.
    return call if isinstance(call, str) and call else None


class PublicChildren:
    def __init__(self, thread_id: str, visibility: ChildVisibility) -> None:
        self.thread_id = thread_id
        self.visibility = visibility
        self.active: dict[Namespace, str] = {}
        self.finished: set[Namespace] = set()
        self._task_handles: dict[str, str] = {}

    @property
    def task_handles(self) -> dict[str, str]:
        return self._task_handles.copy()

    def allow_tool(self, name: str | None, tool_id: str) -> bool:
        if self.visibility.allow_legacy_details:
            return True
        if not name or name == DELEGATION_TOOL:
            return tool_id in self.visibility.tool_call_ids
        return True

    def allow_scoped_tool(self, name: str | None, tool_id: str, owner: str | None) -> bool:
        if self.visibility.allow_legacy_details or (name and name != DELEGATION_TOOL):
            return True
        # Provider call IDs are local to the issuing model. A sibling model
        # can reuse the same ID for another, unauthorized delegation.
        matches = [
            ns
            for call_id, ns, _ in self.visibility.bindings
            if call_id == tool_id and ns in self.visibility.namespaces and ns
        ]
        historical = {
            ns
            for call_id, ns in self.visibility.historical_bindings
            if call_id == tool_id and ns in self.visibility.namespaces and ns
        }
        # Keep duplicate durable rows ambiguous. An independently proven
        # historical relation permits replay only for this same call and scope.
        scopes = set(matches) | historical
        if len(matches) > 1 or len(scopes) != 1:
            return False
        child = next(iter(scopes))
        # LangGraph may insert structural graph segments between two actual
        # subagents. Only an independently authorized child row denotes an issuer.
        parent = next(
            (
                child[:n]
                for n in range(len(child) - 1, 0, -1)
                if child[:n] in self.visibility.namespaces
            ),
            (),
        )
        return self.owner(parent) == owner

    def historical_only(self, namespace: Namespace) -> bool:
        if self.visibility.allow_legacy_details or not namespace:
            return False
        return any(ns == namespace for _, ns in self.visibility.historical_bindings) and not any(
            ns == namespace for _, ns, _ in self.visibility.bindings
        )

    def visible(self, namespace: Namespace) -> bool:
        if not namespace or self.visibility.allow_legacy_details:
            return True
        # A grant to one child must not reveal a nested or sibling child's content.
        return namespace in self.visibility.namespaces

    def owner(self, namespace: Namespace) -> str | None:
        if not namespace:
            return None
        return public_subagent_id(self.thread_id, namespace)

    def start(
        self,
        namespace: Namespace,
        data: Mapping[str, Any],
        *,
        tool_id: Callable[[str | None, str | None], str | None],
        tool_message: Callable[[str | None, str | None], str | None],
    ) -> list[BaseEvent]:
        if not namespace or namespace in self.active or namespace in self.finished:
            return []
        cause = parent_call(data)
        if not self.visible(namespace):
            return []
        parent = self.parent(namespace)
        owner = public_subagent_id(self.thread_id, namespace)
        public_call = tool_id(parent, cause) if cause else None
        if public_call and cause:
            matches = {
                task_id
                for call_id, ns, task_id in self.visibility.bindings
                if call_id == cause and ns == namespace
            }
            if len(matches) == 1:
                self._task_handles[owner] = matches.pop()
            elif not (
                self.visibility.allow_legacy_details
                or (not matches and (cause, namespace) in self.visibility.historical_bindings)
            ):
                # Independent grants for this call and this namespace do not
                # establish that the former actually spawned the latter.
                public_call = None
        name = data.get("graph_name") or data.get("agent_name") or data.get("name")
        self.active[namespace] = owner
        return [
            SubagentStartedEvent(
                subagent_run_id=owner,
                name=name[:160] if isinstance(name, str) and name else "subagent",
                parent_subagent_run_id=parent,
                parent_tool_call_id=public_call,
                parent_message_id=tool_message(parent, cause) if public_call else None,
            )
        ]

    def parent(self, namespace: Namespace) -> str | None:
        for n in range(len(namespace) - 1, 0, -1):
            if namespace[:n] in self.active:
                return self.active[namespace[:n]]
        return None

    def end(
        self,
        namespace: Namespace,
        status: str,
        *,
        close_entities: Callable[[str | None], list[BaseEvent]],
        interrupt_ids: Callable[[str], Sequence[str]],
    ) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        # A parent can close while the graph still has children (notably on
        # interrupt). Close precisely that subtree, deepest invocation first.
        subtree = sorted(
            (ns for ns in self.active if ns[: len(namespace)] == namespace),
            key=len,
            reverse=True,
        )
        for ns in subtree:
            owner = self.active.pop(ns)
            self.finished.add(ns)
            events.extend(close_entities(owner))
            if status in {"failed", "stale"}:
                events.append(SubagentErrorEvent(subagent_run_id=owner, message="Subagent failed."))
                continue
            if status in {"canceled", "cancelled"}:
                events.append(
                    SubagentErrorEvent(
                        subagent_run_id=owner, message="Subagent cancelled.", code="CANCELLED"
                    )
                )
                continue
            ids = list(interrupt_ids(owner))
            outcome = None
            if status == "interrupted":
                outcome = SubagentFinishedSuspendedOutcome(type="suspended", interrupt_ids=ids)
            events.append(SubagentFinishedEvent(subagent_run_id=owner, outcome=outcome))
        return events
