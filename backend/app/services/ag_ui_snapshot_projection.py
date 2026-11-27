"""Materialize public AG-UI frames for one authorized physical run."""

from collections.abc import Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import UUID

type Row = dict[str, object]
type Frame = Mapping[str, object]


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass
class _Call:
    id: str
    name: str
    parent: str
    arguments: list[str] = field(default_factory=list)

    def message_call(self) -> Row:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": "".join(self.arguments)},
        }


class AgUiRunSnapshot:
    """Consume frames emitted by AgUiV1Projector, never raw journal rows or graph state."""

    def __init__(
        self,
        *,
        public_activity_types: Collection[str] = (),
        physical_run_id: str | None = None,
    ) -> None:
        self._activity_types = frozenset(public_activity_types)
        self._physical_run_id = physical_run_id
        self._order: list[str] = []
        self._rows: dict[str, Row] = {}
        self._links: dict[str, Row] = {}
        self._text: dict[str, list[str]] = {}
        self._calls: dict[tuple[str | None, str], list[_Call]] = {}
        self._calls_by_message: dict[str, list[_Call]] = {}
        self._last_assistant: dict[str | None, str] = {}
        self._children: dict[str, Row] = {}
        self._todos: dict[str | None, list[Row]] = {}

    def messages(self) -> list[Row]:
        result: list[Row] = []
        for mid in self._order:
            row = deepcopy(self._rows[mid])
            if mid in self._text:
                row["content"] = "".join(self._text[mid])
            if mid in self._calls_by_message:
                row["toolCalls"] = [call.message_call() for call in self._calls_by_message[mid]]
            result.append(row)
        return result

    def subagents(self) -> list[Row]:
        return deepcopy(list(self._children.values()))

    def links_by_id(self) -> dict[str, Row]:
        return deepcopy(self._links)

    def todos_by_owner(self) -> dict[str | None, list[Row]]:
        return deepcopy(self._todos)

    def _put(
        self,
        mid: str,
        row: Row,
        owner: str | None,
        ordinal: int | None,
        *,
        parent: str | None = None,
        call_id: str | None = None,
    ) -> None:
        if mid not in self._rows:
            self._order.append(mid)
        link: Row = {}
        if owner:
            row["subagentRunId"] = link["subagentRunId"] = owner
            child = self._children.get(owner, {})
            for key in ("parentToolCallId", "parentMessageId"):
                if key in child:
                    link[key] = child[key]
        if parent:
            link["parentMessageId"] = parent
        if call_id:
            link["parentToolCallId"] = call_id
        if ordinal is not None:
            link["sourceOrdinal"] = ordinal
        self._rows[mid] = row
        self._links[mid] = link

    def apply(self, frame: Frame, *, source_ordinal: int | None = None) -> None:
        owner = _string(frame.get("subagentRunId"))
        mid = _string(frame.get("messageId"))
        call_id = _string(frame.get("toolCallId"))
        event_type = frame.get("type")
        match event_type:
            case "TEXT_MESSAGE_START" if mid:
                role = frame.get("role", "assistant")
                if role not in ("assistant", "user"):
                    return
                if mid not in self._rows:
                    row: Row = {"id": mid, "role": role, "content": ""}
                    if name := _string(frame.get("name")):
                        row["name"] = name
                    self._put(mid, row, owner, source_ordinal)
                if role == "assistant" and self._matches(mid, role, owner):
                    self._last_assistant[owner] = mid
            case "TEXT_MESSAGE_CONTENT" if mid:
                delta = frame.get("delta")
                row = self._rows.get(mid, {})
                if (
                    isinstance(delta, str)
                    and row.get("role") in ("assistant", "user")
                    and row.get("subagentRunId") == owner
                ):
                    self._text.setdefault(mid, []).append(delta)
            case "TOOL_CALL_START" if call_id:
                self._start_call(frame, call_id, owner, source_ordinal)
            case "TOOL_CALL_ARGS" if call_id:
                calls = self._calls.get((owner, call_id))
                delta = frame.get("delta")
                if calls and isinstance(delta, str):
                    calls[-1].arguments.append(delta)
            case "TOOL_CALL_RESULT" if call_id and mid:
                self._tool_result(frame, mid, call_id, owner, source_ordinal)
            case "ACTIVITY_SNAPSHOT" if mid:
                self._activity(frame, mid, owner, source_ordinal)
            case "STATE_SNAPSHOT":
                self._state(frame, owner)
            case "SUBAGENT_STARTED" if owner:
                self._start_child(frame, owner)
            case "SUBAGENT_FINISHED" | "SUBAGENT_ERROR" if owner and owner in self._children:
                self._finish_child(frame, self._children[owner])
            case "RUN_FINISHED" | "RUN_ERROR":
                self._finish_open_children(frame)

    def _matches(self, mid: str, role: str, owner: str | None) -> bool:
        row = self._rows.get(mid, {})
        return row.get("role") == role and row.get("subagentRunId") == owner

    def _start_call(self, frame: Frame, cid: str, owner: str | None, ordinal: int | None) -> None:
        name = _string(frame.get("toolCallName"))
        if not name:
            return
        mid = _string(frame.get("parentMessageId")) or cid
        if mid in self._rows and not self._matches(mid, "assistant", owner):
            mid = cid
        if mid not in self._rows:
            self._put(mid, {"id": mid, "role": "assistant"}, owner, ordinal)
        if not self._matches(mid, "assistant", owner):
            return
        call = _Call(cid, name, mid)
        self._calls.setdefault((owner, cid), []).append(call)
        self._calls_by_message.setdefault(mid, []).append(call)

    def _tool_result(
        self, frame: Frame, mid: str, cid: str, owner: str | None, ordinal: int | None
    ) -> None:
        content = frame.get("content")
        if not isinstance(content, str):
            return
        existing = self._rows.get(mid)
        if existing and (existing["role"] != "tool" or existing.get("toolCallId") != cid):
            return
        # A result's lane is the executor. The issuing assistant can belong to its parent.
        calls = self._calls.get((owner, cid), [])
        if not calls:
            calls = [
                call for (_, key), group in self._calls.items() if key == cid for call in group
            ]
        issuer = calls[0].parent if len(calls) == 1 else None
        self._put(
            mid,
            {"id": mid, "toolCallId": cid, "role": "tool", "content": content},
            owner,
            ordinal,
            parent=issuer,
            call_id=cid,
        )
        if issuer and existing is None:
            self._order.pop()
            index = self._order.index(issuer) + 1
            while index < len(self._order) and self._rows[self._order[index]]["role"] in (
                "activity",
                "tool",
            ):
                index += 1
            self._order.insert(index, mid)

    def _activity(self, frame: Frame, mid: str, owner: str | None, ordinal: int | None) -> None:
        kind = _string(frame.get("activityType"))
        content = frame.get("content")
        if kind not in self._activity_types or not isinstance(content, dict):
            return
        existing = self._rows.get(mid)
        if existing and (
            existing["role"] != "activity"
            or existing.get("subagentRunId") != owner
            or frame.get("replace") is False
        ):
            return
        related = _string(content.get("public_message_id"))
        parent = related if related and self._matches(related, "assistant", owner) else None
        if not parent and kind == "moldy.compaction":
            parent = self._last_assistant.get(owner)
        call_id = None
        if kind in ("moldy.ui_data", "moldy.mcp_app"):
            if "public_message_id" in content and not parent:
                return
            if (
                kind == "moldy.ui_data"
                and "message_id" in content
                and (not parent or content["message_id"] != parent)
            ):
                return
            if (
                kind == "moldy.mcp_app"
                or "tool_call_id" in content
                or "public_tool_call_id" in content
            ):
                call_id = _string(content.get("public_tool_call_id"))
                calls = self._calls.get((owner, call_id), []) if call_id else []
                if len(calls) != 1 or related not in (None, calls[0].parent):
                    return
                if kind == "moldy.ui_data" and content.get("tool_call_id") != call_id:
                    return
                parent = calls[0].parent
        if kind == "moldy.subagent_todos" and owner:
            self._state({"snapshot": content}, owner)
        if kind == "moldy.mcp_app":
            protected_id = _string(content.get("tool_call_id"))
            binding_id = _string(content.get("binding_id"))
            if (
                not protected_id
                or not binding_id
                or not self._physical_run_id
                or content.get("run_id") != self._physical_run_id
            ):
                return
            try:
                UUID(binding_id)
            except ValueError:
                return
            content = {
                "tool_call_id": protected_id,
                "public_tool_call_id": call_id,
                "binding_id": binding_id,
                "run_id": self._physical_run_id,
            }
            if related:
                content["public_message_id"] = related
        row: Row = {
            "id": mid,
            "role": "activity",
            "activityType": kind,
            "content": deepcopy(content),
        }
        self._put(mid, row, owner, ordinal, parent=parent, call_id=call_id)

    def _start_child(self, frame: Frame, owner: str) -> None:
        name = _string(frame.get("name"))
        if not name or owner in self._children:
            return
        child: Row = {"subagentRunId": owner, "name": name, "status": "running"}
        if description := _string(frame.get("description")):
            child["description"] = description
        enclosing = _string(frame.get("parentSubagentRunId"))
        if enclosing:
            child["parentSubagentRunId"] = enclosing
        parent = _string(frame.get("parentMessageId"))
        if cid := _string(frame.get("parentToolCallId")):
            calls = self._calls.get((enclosing, cid), [])
            if len(calls) == 1 and parent in (None, calls[0].parent):
                child.update(parentToolCallId=cid, parentMessageId=calls[0].parent)
        elif parent and self._matches(parent, "assistant", enclosing):
            child["parentMessageId"] = parent
        self._children[owner] = child

    @staticmethod
    def _finish_child(frame: Frame, child: Row) -> None:
        if child["status"] != "running":
            return
        if frame["type"] == "SUBAGENT_ERROR":
            child["status"] = "cancelled" if frame.get("code") == "CANCELLED" else "failed"
            if error := _string(frame.get("message")):
                child["error"] = error
            return
        outcome = frame.get("outcome")
        if isinstance(outcome, dict) and outcome.get("type") == "suspended":
            child["status"] = "interrupted"
            ids = outcome.get("interruptIds")
            if isinstance(ids, list):
                child["interruptIds"] = [item for item in ids if isinstance(item, str) and item]
        else:
            child["status"] = "completed"
            if result := _string(frame.get("result")):
                child["result"] = result

    def _finish_open_children(self, frame: Frame) -> None:
        outcome = frame.get("outcome")
        reason = _string(outcome.get("type")) if isinstance(outcome, dict) else None
        status = {"cancelled": "cancelled", "interrupt": "interrupted"}.get(reason or "")
        if frame["type"] == "RUN_ERROR":
            status = "failed"
        if status:
            for child in self._children.values():
                if child["status"] == "running":
                    child["status"] = status
        if status == "interrupted" and isinstance(outcome, dict):
            interrupts = outcome.get("interrupts")
            if isinstance(interrupts, list):
                for item in interrupts:
                    if not isinstance(item, dict):
                        continue
                    owner, iid = _string(item.get("subagentRunId")), _string(item.get("id"))
                    child = self._children.get(owner) if owner else None
                    if iid and child and child["status"] == status:
                        ids = child.setdefault("interruptIds", [])
                        if isinstance(ids, list) and iid not in ids:
                            ids.append(iid)

    def _state(self, frame: Frame, owner: str | None) -> None:
        state = frame.get("snapshot")
        if not isinstance(state, dict) or not isinstance(todos := state.get("todos"), list):
            return
        public: list[Row] = []
        for todo in todos:
            if not isinstance(todo, dict):
                continue
            text = _string(todo.get("content"))
            status = todo.get("status")
            if text and status in ("pending", "in_progress", "completed"):
                row: Row = {"content": text, "status": status}
                if task_id := _string(todo.get("id")):
                    row["id"] = task_id
                public.append(row)
        self._todos[owner] = public
