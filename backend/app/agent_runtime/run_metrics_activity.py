"""Replay-safe tool, subagent, and compact activity aggregation."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.agent_runtime.protocol_usage_normalization import text_value
from app.agent_runtime.run_metrics_types import MessageIdentity, RunMetricActivity


class RunActivityAccumulator:
    """Mutable bounded activity tracker for one incrementally observed run."""

    def __init__(self, *, complete_event_capture: bool, activity_limit: int) -> None:
        self._complete_event_capture = complete_event_capture
        self._activity_limit = max(activity_limit, 0)
        self._tool_call_ids: set[MessageIdentity] = set()
        self._subagent_call_ids: set[MessageIdentity] = set()
        self._model_activity_ids: set[MessageIdentity] = set()
        self._unknown_root_tool_calls = False
        self._unknown_descendant_tool_calls = False
        self._unknown_root_subagent_calls = False
        self._unknown_descendant_subagent_calls = False
        self._activity: deque[RunMetricActivity] = deque()
        self._activity_truncated = False

    def observe(self, event: StoredProtocolEvent, *, elapsed_ms: float | None) -> None:
        namespace = tuple(event["namespace"])
        self._observe_tool_call(event, namespace, elapsed_ms)
        self._observe_subagent_event(event, namespace, elapsed_ms)

    def record_model_usage(
        self,
        identity: MessageIdentity,
        *,
        elapsed_ms: float | None,
    ) -> None:
        if identity in self._model_activity_ids:
            return
        self._model_activity_ids.add(identity)
        self._append_activity(
            RunMetricActivity(
                kind="model_usage",
                namespace=identity[0],
                call_id=identity[1],
                name=None,
                elapsed_ms=elapsed_ms,
            )
        )

    @property
    def activity(self) -> tuple[RunMetricActivity, ...]:
        return tuple(self._activity)

    @property
    def activity_truncated(self) -> bool:
        return self._activity_truncated

    @property
    def root_tool_calls(self) -> int | None:
        return self._call_count(self._tool_call_ids, root=True)

    @property
    def descendant_tool_calls(self) -> int | None:
        return self._call_count(self._tool_call_ids, root=False)

    @property
    def root_subagent_calls(self) -> int | None:
        return self._call_count(self._subagent_call_ids, root=True)

    @property
    def descendant_subagent_calls(self) -> int | None:
        return self._call_count(self._subagent_call_ids, root=False)

    def _observe_tool_call(
        self,
        event: StoredProtocolEvent,
        namespace: tuple[str, ...],
        elapsed_ms: float | None,
    ) -> None:
        if event["method"] != "tools" or not isinstance(event["data"], Mapping):
            return
        data = event["data"]
        if data.get("event") != "tool-started":
            return
        call_id = text_value(data.get("tool_call_id"))
        name = text_value(data.get("name"))
        if call_id is None:
            self._mark_unknown(namespace, subagent=False)
            return
        identity = (namespace, call_id)
        if identity not in self._tool_call_ids:
            self._tool_call_ids.add(identity)
            self._append_activity(
                RunMetricActivity(
                    kind="tool_call",
                    namespace=namespace,
                    call_id=call_id,
                    name=name,
                    elapsed_ms=elapsed_ms,
                )
            )
        if name == "task":
            self._record_subagent_call(identity, name, elapsed_ms)

    def _observe_subagent_event(
        self,
        event: StoredProtocolEvent,
        namespace: tuple[str, ...],
        elapsed_ms: float | None,
    ) -> None:
        if event["method"] not in {"tasks", "subagents", "subgraphs", "lifecycle"}:
            return
        if not isinstance(event["data"], Mapping):
            return
        data = event["data"]
        if event["method"] == "lifecycle" and not any(
            key in data
            for key in (
                "trigger_call_id",
                "tool_call_id",
                "id",
                "name",
                "agent_name",
                "graph_name",
                "path",
                "cause",
            )
        ):
            return
        call_id = text_value(
            data.get("trigger_call_id") or data.get("tool_call_id") or data.get("id")
        )
        if call_id is None:
            self._mark_unknown(namespace, subagent=True)
            return
        self._record_subagent_call(
            (namespace, call_id),
            text_value(data.get("name")),
            elapsed_ms,
        )

    def _record_subagent_call(
        self,
        identity: MessageIdentity,
        name: str | None,
        elapsed_ms: float | None,
    ) -> None:
        if identity in self._subagent_call_ids:
            return
        self._subagent_call_ids.add(identity)
        self._append_activity(
            RunMetricActivity(
                kind="subagent_call",
                namespace=identity[0],
                call_id=identity[1],
                name=name,
                elapsed_ms=elapsed_ms,
            )
        )

    def _mark_unknown(self, namespace: tuple[str, ...], *, subagent: bool) -> None:
        if namespace:
            if subagent:
                self._unknown_descendant_subagent_calls = True
            else:
                self._unknown_descendant_tool_calls = True
        elif subagent:
            self._unknown_root_subagent_calls = True
        else:
            self._unknown_root_tool_calls = True

    def _append_activity(self, activity: RunMetricActivity) -> None:
        if self._activity_limit == 0:
            self._activity_truncated = True
            return
        if len(self._activity) == self._activity_limit:
            self._activity.popleft()
            self._activity_truncated = True
        self._activity.append(activity)

    def _call_count(self, calls: set[MessageIdentity], *, root: bool) -> int | None:
        if not self._complete_event_capture:
            return None
        unknown_root = (
            self._unknown_root_tool_calls
            if calls is self._tool_call_ids
            else self._unknown_root_subagent_calls
        )
        if root and unknown_root:
            return None
        if not root and (
            self._unknown_descendant_tool_calls
            if calls is self._tool_call_ids
            else self._unknown_descendant_subagent_calls
        ):
            return None
        return sum(1 for namespace, _call_id in calls if bool(namespace) is not root)
