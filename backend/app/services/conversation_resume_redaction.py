"""Resolve edited tool arguments from the actions recorded at an approval checkpoint."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

REDACTED_PLACEHOLDER = "<redacted>"
_MISSING = object()


class RedactedResumeArgsUnavailable(RuntimeError):
    pass


def action_requests(value: Any) -> list[dict[str, Any]]:
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


def restore_redacted_response(
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
        # langchain reads ``edited_action["name"]`` as a hard subscript. The
        # frontend may omit name (backend fills by index); if neither the pending
        # action nor the client supplied one, fail closed rather than let the
        # resume crash with a KeyError deep in the middleware.
        if not isinstance(restored_edited_action.get("name"), str):
            raise RedactedResumeArgsUnavailable()
        restored_decisions.append({**dict(decision), "edited_action": restored_edited_action})
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
