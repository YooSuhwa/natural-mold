"""Project one authorized, ordered run journal onto the public AG-UI 1.0 wire."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from ag_ui.core import BaseEvent, RunErrorEvent, RunStartedEvent

from app.agent_runtime.ag_ui_v1_activities import PublicActivities
from app.agent_runtime.ag_ui_v1_children import (
    Namespace,
    PublicChildren,
    public_journal_namespace,
    public_subagent_id,
)
from app.agent_runtime.ag_ui_v1_entities import PublicChatEntities
from app.agent_runtime.ag_ui_v1_issuance import ToolContinuation, ToolSnapshotSources
from app.agent_runtime.ag_ui_v1_outcomes import PendingInterrupts, run_terminal_event
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.protocol_events import (
    ProtocolInterrupt,
    StoredProtocolEvent,
    protocol_interrupts_from_event,
)

AG_UI_PROJECTION_VERSION = "1"

__all__ = [
    "AG_UI_PROJECTION_VERSION",
    "AgUiV1Projector",
    "ChildVisibility",
    "ProjectionVisibilityChanged",
    "public_subagent_id",
]


class ProjectionVisibilityChanged(ValueError):
    """The authorized snapshot must be rebuilt before continuing this stream."""


class AgUiV1Projector:
    def __init__(
        self,
        *,
        thread_id: str,
        run_id: str,
        source_run_id: str | None = None,
        parent_run_id: str | None = None,
        secret_values: Iterable[str] = (),
        tool_continuations: Sequence[ToolContinuation] = (),
        child_visibility: ChildVisibility | None = None,
        readable_artifact_versions: frozenset[tuple[str, str]] = frozenset(),
        defer_terminal: bool = False,
    ) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.source_run_id = source_run_id or run_id
        self.parent_run_id = parent_run_id
        self._secrets = tuple(secret_values)
        self._children = PublicChildren(
            thread_id, child_visibility or ChildVisibility(True, frozenset(), frozenset())
        )
        self._tool_snapshot_sources = ToolSnapshotSources()
        self._entities = PublicChatEntities(
            run_id,
            self._children.allow_tool,
            secret_values=self._secrets,
            tool_continuations=tool_continuations,
            allow_scoped_tool=self._children.allow_scoped_tool,
        )
        self._activities = PublicActivities(readable_artifact_versions=readable_artifact_versions)
        self._pending = PendingInterrupts()
        self._defer_terminal = defer_terminal
        self._hidden_interrupts: set[str] = set()
        self._hidden_child = False
        self._early_state: dict[Namespace, list[BaseEvent]] = {}
        self._early_calls: dict[Namespace, list[BaseEvent]] = {}
        self._slot_scopes: dict[str, Namespace] = {}
        self._started = False
        self._journal_read = False
        self.closed = False

    @property
    def root_issuances(self) -> dict[tuple[str | None, str], str]:
        return self._entities.root_issuances

    @property
    def unresolved_tool_calls(self) -> tuple[ToolContinuation, ...]:
        return self._entities.unresolved_tool_calls

    def owner_for(self, namespace: Namespace) -> str | None:
        if not self._children.visible(namespace):
            return None
        return self._children.owner(namespace)

    @property
    def child_task_handles(self) -> dict[str, str]:
        return self._children.task_handles

    def set_child_visibility(self, visibility: ChildVisibility) -> None:
        if (self._started or self._journal_read) and visibility != self._children.visibility:
            raise ProjectionVisibilityChanged("child visibility changed during replay")
        self._children.visibility = visibility

    def start(self) -> list[BaseEvent]:
        if self._started or self.closed:
            return []
        self._started = True
        return [
            RunStartedEvent(
                thread_id=self.thread_id,
                run_id=self.run_id,
                parent_run_id=self.parent_run_id,
                protocol_version="1.0",
            )
        ]

    def _open_child(self, namespace: Namespace, data: Mapping[str, Any]) -> list[BaseEvent]:
        opened = self._children.start(
            namespace,
            data,
            tool_id=self._entities.public_delegation_tool_id,
            tool_message=self._entities.tool_parent_message_id,
        )
        if opened:
            opened.extend(self._early_state.pop(namespace, []))
            opened.extend(self._early_calls.pop(namespace, []))
        return opened

    def _close_children(self, namespace: Namespace, status: str) -> list[BaseEvent]:
        return self._children.end(
            namespace,
            status,
            close_entities=self._entities.close,
            interrupt_ids=self._pending.for_owner,
        )

    def _lifecycle(self, namespace: Namespace, data: Mapping[str, Any]) -> list[BaseEvent]:
        status = data.get("event") or data.get("status") or data.get("state")
        if not isinstance(status, str):
            return []
        status = {
            "error": "failed",
            "success": "completed",
            "complete": "completed",
            "requires_action": "interrupted",
        }.get(status, status)
        if namespace:
            if status in {"running", "started"}:
                frames = self._open_child(namespace, data)
                return [*self.start(), *frames] if frames else []
            if status in {"completed", "failed", "interrupted", "canceled", "cancelled"}:
                if namespace in self._early_calls or namespace in self._early_state:
                    return [
                        *self.start(),
                        *self._open_child(namespace, data),
                        *self._close_children(namespace, status),
                    ]
                return self._close_children(namespace, status)
            return []
        if status in {"started", "running"}:
            return self.start()
        if self._defer_terminal:
            return []
        if status in {"completed", "failed", "interrupted", "canceled", "cancelled", "stale"}:
            return self.finish(status)
        return []

    def _hide_interrupt(self, interrupt_id: str) -> None:
        # A serializer may first repeat a child slot without its namespace.
        self._hidden_interrupts.add(interrupt_id)
        self._pending.discard(interrupt_id)

    def project(self, event: StoredProtocolEvent) -> list[BaseEvent]:
        if self.closed:
            return []
        if event["thread_id"] != self.thread_id or event["run_id"] != self.source_run_id:
            raise ValueError("journal event belongs to a different run")
        self._journal_read = True
        method = event["method"]
        data = event["data"]
        namespace = public_journal_namespace(event)
        if namespace is None:
            return []
        lifecycle = method in {"lifecycle", "subagents", "subgraphs"}
        if not self._children.visible(namespace):
            self._hidden_child = True
            for item in protocol_interrupts_from_event(event):
                self._hide_interrupt(item["id"])
            return []
        if method == "error":
            if namespace:
                return self._close_children(namespace, "failed")
            return [] if self._defer_terminal else self.finish("failed")
        if not (
            lifecycle
            or method
            in {"messages", "tools", "input.requested", "values", "updates", "tasks", "custom"}
            or method.startswith("custom:")
        ):
            return []
        if isinstance(data, Mapping) and not self._children.visibility.allow_legacy_details:
            name = (
                data.get("name") or data.get("channel")
                if method == "custom"
                else method.removeprefix("custom:")
            )
            if method.startswith("custom") and name in ("moldy.subagent_names", "subagent_names"):
                return []
        if method == "values" and namespace not in self._children.finished:
            self._activities.observe_values_parent(
                event, subagent_run_id=self._children.owner(namespace)
            )
        owner = self._children.owner(namespace)
        if method == "values":
            self._tool_snapshot_sources.observe_values(event, owner)
        elif method == "tools" and isinstance(data, Mapping):
            synthetic, source_id = self._tool_snapshot_sources.synthesized_source(event, owner)
            if synthetic and not self._entities.permits_synthetic_tool(
                owner, data.get("tool_call_id"), source_id
            ):
                return []
        if method in {"values", "updates", "tasks"} and isinstance(data, Mapping):
            # Checkpoints can contain a full historical transcript, attachments and
            # private agent state. Only current todos and interrupt slots have a
            # wire representation here.
            data = {
                key: data[key] for key in ("todos", "interrupts", "__interrupt__") if key in data
            }
        safe_event: StoredProtocolEvent = {
            **event,
            "data": project_and_redact_protocol_data(method, data, secret_values=self._secrets),
        }
        if lifecycle and isinstance(safe_event["data"], Mapping):
            return self._lifecycle(namespace, safe_event["data"])
        for item in protocol_interrupts_from_event(safe_event):
            ns = tuple(item["ns"]) or namespace
            if not self._children.visible(ns) or self._children.historical_only(ns):
                # A read-only relation proves where old tool results belong,
                # never that the former approval slot can still be answered.
                self._hide_interrupt(item["id"])
            elif item["id"] not in self._hidden_interrupts:
                owner = self._children.owner(ns)
                if owner:
                    self._slot_scopes[owner] = ns
                self._pending.add(item, subagent_run_id=owner)
        if namespace in self._children.finished:
            return []

        frames = self._entities.project(safe_event, owner=owner)
        self._activities.observe_visible_messages(frames)
        mid, call = self._activity_ids(safe_event, owner)
        frames.extend(
            self._activities.project(
                safe_event,
                run_id=self.run_id,
                subagent_run_id=owner,
                public_message_id=mid,
                public_tool_call_id=call,
            )
        )
        if namespace and namespace not in self._children.active and method in {"values", "updates"}:
            # The first checkpoint can precede the lifecycle carrying the
            # actual caller. Retain its latest public todo state until then.
            if frames:
                self._early_state[namespace] = frames
            return []
        if not frames:
            return []
        # On an actual approval continuation the runtime can synthesize a
        # child's native tool-start from its checkpoint immediately before the
        # lifecycle that identifies the caller. Keep that short public preamble.
        if (
            namespace
            and namespace not in self._children.active
            and method == "tools"
            and isinstance(data, Mapping)
            and data.get("event") == "tool-started"
            and len(self._early_calls.get(namespace, [])) < 12
        ):
            self._early_calls.setdefault(namespace, []).extend(frames)
            return []
        headers = self.start()
        if namespace:
            # Fallback streams can have child messages but no lifecycle. Open
            # the invocation before its first *visible* frame, never on raw state.
            headers.extend(self._open_child(namespace, {}))
        return [*headers, *frames]

    def _activity_ids(
        self, event: StoredProtocolEvent, owner: str | None
    ) -> tuple[str | None, str | None]:
        data = event["data"]
        if event["method"] == "messages" and isinstance(data, (tuple, list)) and len(data) == 2:
            data = data[0]
        if not isinstance(data, Mapping):
            return None, None
        payload = data.get("payload")
        if not isinstance(payload, Mapping):
            payload = data
        name = (
            data.get("name") or data.get("channel")
            if event["method"] == "custom"
            else event["method"].removeprefix("custom:")
        )
        if name in ("moldy.ui_data", "ui_data"):
            raw_call = payload.get("tool_call_id")
            raw_message = payload.get("message_id")
            claimed_message = (
                self._entities.known_message_id(owner, raw_message)
                if isinstance(raw_message, str)
                else None
            )
            if not isinstance(raw_call, str):
                return claimed_message, None
            parent = self._entities.tool_parent_message_id(owner, raw_call)
            if raw_message is not None and (not claimed_message or claimed_message != parent):
                return None, None
            return parent, self._entities.public_tool_id(owner, raw_call)
        call = payload.get("tool_call_id") or payload.get("toolCallId")
        if isinstance(call, str):
            return self._entities.tool_parent_message_id(
                owner, call
            ), self._entities.public_tool_id(owner, call)
        mid = payload.get("assistant_msg_id") or payload.get("message_id")
        if event["method"] == "messages":
            mid = mid or data.get("id")
            if mid is None and data.get("event") in ("message-start", "message-finish"):
                return self._entities.current_observed_message_id(owner), None
        return self._entities.observed_message_id(
            owner, mid if isinstance(mid, str) else None
        ), None

    def finish(
        self,
        status: str,
        *,
        interrupts: Sequence[ProtocolInterrupt] | None = None,
        error_code: str | None = None,
        expires_at: Mapping[str, str] | None = None,
        legacy_child_ids: Collection[str] = (),
        passive: bool = False,
    ) -> list[BaseEvent]:
        if self.closed or status in {"queued", "running", "canceling"}:
            return []
        if interrupts is not None:
            safe_interrupts: list[ProtocolInterrupt] = []
            for item in interrupts:
                if (
                    not self._children.visible(tuple(item["ns"]))
                    or self._children.historical_only(tuple(item["ns"]))
                    or item["id"] in self._hidden_interrupts
                ):
                    # Resume must resolve ALL current slots. A partial set would
                    # present actions the caller cannot submit successfully.
                    passive = True
                    continue
                safe_value = project_and_redact_protocol_data(
                    "input.requested", item["value"], secret_values=self._secrets
                )
                ns = tuple(item["ns"])
                owner = self.owner_for(ns)
                if owner:
                    self._slot_scopes[owner] = ns
                safe_interrupts.append({"id": item["id"], "value": safe_value, "ns": item["ns"]})
            self._pending.replace_current(
                safe_interrupts,
                resolve_owner=self.owner_for,
                expires_at=expires_at,
                passive=passive,
                legacy_child_ids=legacy_child_ids
                if self._children.visibility.allow_legacy_details
                else (),
            )
        elif (self._hidden_interrupts or passive) and status == "interrupted":
            self._pending.mark_passive()
        terminal = run_terminal_event(
            self.thread_id,
            self.run_id,
            status,
            self._pending,
            error_code=error_code,
            usage=None if self._hidden_child else self._activities.terminal_usage(),
        )
        if not self._started and isinstance(terminal, RunErrorEvent):
            self.closed = True
            return [terminal]
        frames = self.start()
        unopened = set(self._early_calls) | set(self._early_state)
        if status == "interrupted":
            unopened.update(
                self._slot_scopes[item.subagent_run_id]
                for item in self._pending.public()
                if item.subagent_run_id in self._slot_scopes
            )
        for ns in sorted(unopened, key=lambda scope: (len(scope), scope)):
            frames.extend(self._open_child(ns, {}))
        frames.extend(self._close_children((), status))
        frames.extend(self._entities.close(None))
        self.closed = True
        return [*frames, terminal]
