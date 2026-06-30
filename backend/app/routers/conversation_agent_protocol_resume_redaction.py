from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_interrupts import ThreadInterrupt
from app.routers.conversation_agent_protocol_resume import ResumePayload

REDACTED_PLACEHOLDER = "<redacted>"
_MISSING = object()


class RedactedResumeArgsUnavailable(RuntimeError):
    pass


async def restore_redacted_resume_payload(
    *,
    conversation: Conversation,
    resume: ResumePayload,
    pending_interrupts: list[ThreadInterrupt],
) -> Any:
    if not _resume_contains_edit(resume.input_payload):
        return resume.input_payload

    raw_actions = await _raw_pending_actions_by_interrupt(conversation, pending_interrupts)
    restored_by_interrupt: dict[str, Any] = {}
    for submitted in resume.submitted:
        restored_by_interrupt[submitted.interrupt_id] = _restore_redacted_response(
            submitted.response,
            raw_actions.get(submitted.interrupt_id, []),
        )

    if isinstance(resume.input_payload, Mapping) and any(
        submitted.interrupt_id in resume.input_payload for submitted in resume.submitted
    ):
        return {
            str(interrupt_id): restored_by_interrupt.get(str(interrupt_id), response)
            for interrupt_id, response in resume.input_payload.items()
        }
    if resume.submitted:
        return restored_by_interrupt.get(resume.submitted[0].interrupt_id, resume.input_payload)
    return resume.input_payload


def _resume_contains_edit(value: Any) -> bool:
    # redacted 유무와 무관하게 edit decision이 하나라도 있으면 index 해석 경로를
    # 타야 한다. edit는 secret placeholder 복원뿐 아니라 ``edited_action.name``을
    # pending action index로 채우는 작업도 필요하기 때문이다(프론트는 name을
    # 보내지 않는다). 비-edit(approve/reject/respond) resume은 그대로 short-circuit.
    if isinstance(value, Mapping):
        if value.get("type") == "edit":
            return True
        return any(_resume_contains_edit(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_resume_contains_edit(item) for item in value)
    return False


async def _raw_pending_actions_by_interrupt(
    conversation: Conversation,
    pending_interrupts: list[ThreadInterrupt],
) -> dict[str, list[dict[str, Any]]]:
    raw_tool_calls = await _raw_latest_tool_calls(conversation)
    by_interrupt: dict[str, list[dict[str, Any]]] = {}
    cursor = 0
    for interrupt in pending_interrupts:
        actions = _action_requests(interrupt.get("value"))
        raw_actions: list[dict[str, Any]] = []
        for action in actions:
            tool_call, cursor = _next_matching_tool_call(raw_tool_calls, action, cursor)
            if tool_call is None:
                raw_actions.append(action)
                continue
            raw_actions.append(
                {
                    "name": tool_call.get("name", action.get("name")),
                    "args": tool_call.get("args", action.get("args", {})),
                }
            )
        by_interrupt[interrupt["id"]] = raw_actions
    return by_interrupt


async def _raw_latest_tool_calls(conversation: Conversation) -> list[dict[str, Any]]:
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.services.thread_branch_service import build_message_tree

    try:
        tree = await build_message_tree(
            get_checkpointer(),
            str(conversation.id),
            active_checkpoint_id=conversation.active_branch_checkpoint_id,
        )
    except Exception:  # noqa: BLE001 - restore failure is reported as a protocol error later
        return []

    for node in reversed(tree.nodes):
        tool_calls = getattr(node.message, "tool_calls", None)
        if not isinstance(tool_calls, Sequence) or isinstance(tool_calls, str | bytes):
            continue
        normalized = [_tool_call_mapping(tool_call) for tool_call in tool_calls]
        return [tool_call for tool_call in normalized if tool_call is not None]
    return []


def _tool_call_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        name = value.get("name")
        args = value.get("args")
    else:
        name = getattr(value, "name", None)
        args = getattr(value, "args", None)
    if not isinstance(name, str) or not isinstance(args, Mapping):
        return None
    return {"name": name, "args": dict(args)}


def _action_requests(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    raw_actions = value.get("action_requests")
    if not isinstance(raw_actions, Sequence) or isinstance(raw_actions, str | bytes):
        return []
    actions: list[dict[str, Any]] = []
    for item in raw_actions:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        args = item.get("args")
        if isinstance(name, str) and isinstance(args, Mapping):
            actions.append({"name": name, "args": dict(args)})
    return actions


def _next_matching_tool_call(
    tool_calls: list[dict[str, Any]],
    action: Mapping[str, Any],
    cursor: int,
) -> tuple[dict[str, Any] | None, int]:
    name = action.get("name")
    for index in range(cursor, len(tool_calls)):
        if tool_calls[index].get("name") == name:
            return tool_calls[index], index + 1
    return None, cursor


def _restore_redacted_response(
    response: Any,
    raw_actions: list[dict[str, Any]],
) -> Any:
    if not isinstance(response, Mapping):
        return response
    decisions = response.get("decisions")
    if not isinstance(decisions, Sequence) or isinstance(decisions, str | bytes):
        return response

    restored_decisions: list[Any] = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, Mapping) or decision.get("type") != "edit":
            restored_decisions.append(decision)
            continue
        edited_action = decision.get("edited_action")
        args = _edited_action_args(decision)
        if not isinstance(edited_action, Mapping) or not isinstance(args, Mapping):
            restored_decisions.append(decision)
            continue
        raw_action = raw_actions[index] if index < len(raw_actions) else None
        raw_args = raw_action.get("args") if isinstance(raw_action, Mapping) else _MISSING
        restored_args, unresolved = _restore_placeholders(args, raw_args)
        if unresolved:
            raise RedactedResumeArgsUnavailable()
        restored_edited_action = {**dict(edited_action), "args": restored_args}
        # 권위적 name 채우기: langchain ``HumanInTheLoopMiddleware``는 decision↔
        # action을 positional index로 매칭하고 ``edited_action["name"]``을 hard
        # subscript로 읽는다. 프론트가 도구 이름을 몰라도(생략/오류) 백엔드가
        # pending action의 이름으로 덮어쓴다. 매칭 raw action이 없으면(방어)
        # 프론트값을 그대로 둔다.
        raw_name = raw_action.get("name") if isinstance(raw_action, Mapping) else None
        if isinstance(raw_name, str):
            restored_edited_action["name"] = raw_name
        restored_decisions.append(
            {**dict(decision), "edited_action": restored_edited_action}
        )
    return {**dict(response), "decisions": restored_decisions}


def _edited_action_args(decision: Mapping[str, Any]) -> Any:
    edited_action = decision.get("edited_action")
    if not isinstance(edited_action, Mapping):
        return None
    return edited_action.get("args")


def _restore_placeholders(value: Any, original: Any) -> tuple[Any, bool]:
    if value == REDACTED_PLACEHOLDER:
        if original is _MISSING or original == REDACTED_PLACEHOLDER:
            return value, True
        return original, False
    if isinstance(value, Mapping):
        original_mapping = original if isinstance(original, Mapping) else {}
        restored: dict[str, Any] = {}
        unresolved = False
        for key, item in value.items():
            original_item = original_mapping.get(key, _MISSING)
            restored_item, item_unresolved = _restore_placeholders(item, original_item)
            restored[str(key)] = restored_item
            unresolved = unresolved or item_unresolved
        return restored, unresolved
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        original_items: Sequence[Any] = (
            original
            if isinstance(original, Sequence) and not isinstance(original, str | bytes | bytearray)
            else []
        )
        restored_items: list[Any] = []
        unresolved = False
        for index, item in enumerate(value):
            original_item = original_items[index] if index < len(original_items) else _MISSING
            restored_item, item_unresolved = _restore_placeholders(item, original_item)
            restored_items.append(restored_item)
            unresolved = unresolved or item_unresolved
        return restored_items, unresolved
    return value, False
