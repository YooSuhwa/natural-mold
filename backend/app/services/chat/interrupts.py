"""HITL interrupt reconstruction for persisted chat messages.

BE-S1 split from ``app.services.chat_service`` — pure move, no behavior
change. Restores pending approval cards from append-only trace chunks when
the message list is rebuilt from the checkpointer.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_event import MessageEvent
from app.schemas.conversation import MessageResponse
from app.services import trace_storage

_HITL_METADATA_KEYS = {
    "approval_id",
    "allowed_decisions",
    "hitl_interrupt_id",
    "hitl_action_index",
    "hitl_total_actions",
}


def _review_config_for_action(
    action: dict[str, Any],
    review_configs: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    if index < len(review_configs):
        return review_configs[index]
    action_name = action.get("name")
    for config in review_configs:
        if config.get("action_name") == action_name:
            return config
    return {"action_name": action_name, "allowed_decisions": ["approve", "reject"]}


def _hitl_metadata_for_action(
    interrupt_id: str,
    review_config: dict[str, Any],
    index: int,
    total_actions: int,
) -> dict[str, Any]:
    approval_id = f"{interrupt_id}:{index}"
    return {
        "approval_id": approval_id,
        "allowed_decisions": review_config.get("allowed_decisions") or ["approve", "reject"],
        "hitl_interrupt_id": interrupt_id,
        "hitl_action_index": index,
        "hitl_total_actions": total_actions,
    }


def _is_ask_user_respond_only(action: dict[str, Any], review_config: dict[str, Any]) -> bool:
    allowed = review_config.get("allowed_decisions") or []
    return action.get("name") == "ask_user" and allowed == ["respond"]


def _standard_interrupt_to_tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    action_requests = payload.get("action_requests")
    review_configs = payload.get("review_configs")
    if not isinstance(action_requests, list) or not isinstance(review_configs, list):
        return []

    interrupt_id = payload.get("interrupt_id")
    if not isinstance(interrupt_id, str):
        interrupt_id = ""

    tool_calls: list[dict[str, Any]] = []
    total_actions = len(action_requests)
    for index, raw_action in enumerate(action_requests):
        if not isinstance(raw_action, dict):
            continue
        action_name = raw_action.get("name")
        if not isinstance(action_name, str) or not action_name:
            continue
        raw_args = raw_action.get("args")
        action_args = raw_args if isinstance(raw_args, dict) else {}
        review_config = _review_config_for_action(raw_action, review_configs, index)
        metadata = _hitl_metadata_for_action(interrupt_id, review_config, index, total_actions)
        if _is_ask_user_respond_only(raw_action, review_config):
            tool_calls.append(
                {
                    "id": metadata["approval_id"],
                    "name": "ask_user",
                    "args": {**action_args, **metadata},
                }
            )
            continue

        args = {
            "tool_name": action_name,
            "tool_args": action_args,
            **metadata,
        }
        description = raw_action.get("description")
        if isinstance(description, str) and description:
            args["description"] = description
        tool_calls.append(
            {
                "id": metadata["approval_id"],
                "name": "request_approval",
                "args": args,
            }
        )
    return tool_calls


def _strip_hitl_metadata(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if key not in _HITL_METADATA_KEYS}


def _args_fingerprint(args: dict[str, Any]) -> str:
    return json.dumps(_strip_hitl_metadata(args), sort_keys=True, ensure_ascii=False, default=str)


def _equivalent_tool_args(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _args_fingerprint(left) == _args_fingerprint(right)


def _contains_tool_args(superset: dict[str, Any], subset: dict[str, Any]) -> bool:
    cleaned_superset = _strip_hitl_metadata(superset)
    cleaned_subset = _strip_hitl_metadata(subset)
    for key, value in cleaned_subset.items():
        if json.dumps(cleaned_superset.get(key), sort_keys=True, default=str) != json.dumps(
            value,
            sort_keys=True,
            default=str,
        ):
            return False
    return True


def _tool_call_name(tool_call: dict[str, Any]) -> str | None:
    name = tool_call.get("name") or tool_call.get("tool_name")
    if isinstance(name, str) and name:
        return name
    function = tool_call.get("function")
    if isinstance(function, dict):
        fn_name = function.get("name")
        if isinstance(fn_name, str) and fn_name:
            return fn_name
    return None


def _tool_call_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    args = tool_call.get("args")
    if isinstance(args, dict):
        return args
    params = tool_call.get("parameters")
    if isinstance(params, dict):
        return params
    function = tool_call.get("function")
    if isinstance(function, dict):
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
    return {}


def _approval_target(
    synthetic: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    if synthetic.get("name") != "request_approval":
        return None
    args = synthetic.get("args")
    if not isinstance(args, dict):
        return None
    tool_name = args.get("tool_name")
    tool_args = args.get("tool_args")
    if not isinstance(tool_name, str) or not tool_name:
        return None
    return tool_name, tool_args if isinstance(tool_args, dict) else {}


def _with_replacement_id(
    synthetic: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    synthetic_id = synthetic.get("id")
    existing_id = existing.get("id")
    fallback_id = existing_id if isinstance(existing_id, str) and existing_id else synthetic_id
    if not isinstance(fallback_id, str) or not fallback_id:
        return synthetic
    args = synthetic.get("args")
    return {
        **synthetic,
        "id": fallback_id,
        "args": {**args, "approval_id": fallback_id} if isinstance(args, dict) else args,
    }


def _merge_interrupt_tool_calls(
    tool_calls: list[dict[str, Any]],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    merged = [dict(call) for call in tool_calls]
    synthetic_tool_calls = _standard_interrupt_to_tool_calls(payload)
    replaced_indices: set[int] = set()

    for synthetic in synthetic_tool_calls:
        was_merged = False

        if synthetic.get("name") == "ask_user":
            synthetic_args = synthetic.get("args")
            if isinstance(synthetic_args, dict):
                for index in range(len(merged) - 1, -1, -1):
                    existing = merged[index]
                    existing_args = _tool_call_args(existing)
                    if _tool_call_name(existing) == "ask_user" and _equivalent_tool_args(
                        existing_args,
                        synthetic_args,
                    ):
                        merged[index] = {
                            **existing,
                            "args": {**existing_args, **synthetic_args},
                        }
                        was_merged = True
                        break

        target = _approval_target(synthetic)
        if not was_merged and target is not None:
            target_name, target_args = target
            for index in range(len(merged) - 1, -1, -1):
                if index in replaced_indices:
                    continue
                existing = merged[index]
                existing_args = _tool_call_args(existing)
                if _tool_call_name(existing) != target_name:
                    continue
                if (
                    _equivalent_tool_args(existing_args, target_args)
                    or _contains_tool_args(target_args, existing_args)
                    or _contains_tool_args(existing_args, target_args)
                    or not existing_args
                    or not target_args
                ):
                    merged[index] = _with_replacement_id(synthetic, existing)
                    replaced_indices.add(index)
                    was_merged = True
                    break

        if not was_merged:
            merged.append(synthetic)

    return merged


def _latest_interrupt_payload(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        data = _interrupt_payload_data(event)
        if data is None:
            continue
        action_requests = data.get("action_requests")
        if isinstance(action_requests, list) and action_requests:
            return data
    return None


def _interrupt_payload_data(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("event") == "interrupt":
        data = event.get("data")
        return data if isinstance(data, dict) else None

    if event.get("method") != "input.requested":
        return None

    data = event.get("data")
    if not isinstance(data, dict):
        params = event.get("params")
        if not isinstance(params, dict):
            return None
        params_data = params.get("data")
        data = params_data if isinstance(params_data, dict) else {}

    if _has_action_requests(data):
        return data

    payload = data.get("payload")
    if not isinstance(payload, dict) or not _has_action_requests(payload):
        return None

    merged = dict(payload)
    interrupt_id = data.get("interrupt_id") or data.get("id")
    namespace = data.get("namespace") or data.get("ns")
    if interrupt_id is not None and "interrupt_id" not in merged:
        merged["interrupt_id"] = interrupt_id
    if namespace is not None and "namespace" not in merged:
        merged["namespace"] = namespace
    return merged


def _has_action_requests(data: dict[str, Any]) -> bool:
    action_requests = data.get("action_requests")
    return isinstance(action_requests, list) and bool(action_requests)


def _completed_tool_call_ids(responses: list[MessageResponse]) -> set[str]:
    return {
        response.tool_call_id
        for response in responses
        if response.role == "tool" and response.tool_call_id
    }


def _interrupt_action_matches_tool_call(
    action: dict[str, Any],
    tool_call: dict[str, Any],
) -> bool:
    action_args = action.get("args")
    target_args = action_args if isinstance(action_args, dict) else {}
    existing_args = _tool_call_args(tool_call)
    if _tool_call_name(tool_call) != action.get("name"):
        return False
    return (
        _equivalent_tool_args(existing_args, target_args)
        or _contains_tool_args(target_args, existing_args)
        or _contains_tool_args(existing_args, target_args)
        or not existing_args
        or not target_args
    )


def _all_interrupt_actions_completed(
    payload: dict[str, Any],
    tool_calls: list[dict[str, Any]],
    completed_tool_call_ids: set[str],
) -> bool:
    action_requests = payload.get("action_requests")
    if not isinstance(action_requests, list) or not action_requests:
        return False
    if not completed_tool_call_ids:
        return False

    for action in action_requests:
        if not isinstance(action, dict):
            return False
        action_completed = any(
            str(tool_call.get("id") or "") in completed_tool_call_ids
            and _interrupt_action_matches_tool_call(action, tool_call)
            for tool_call in tool_calls
        )
        if not action_completed:
            return False
    return True


def _interrupt_payload_targets_tool_calls(
    payload: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> bool:
    action_requests = payload.get("action_requests")
    if not isinstance(action_requests, list) or not action_requests:
        return False
    return any(
        isinstance(action, dict)
        and any(_interrupt_action_matches_tool_call(action, tool_call) for tool_call in tool_calls)
        for action in action_requests
    )


async def _hydrate_pending_interrupt_tool_calls(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    responses: list[MessageResponse],
) -> None:
    """Restore persisted HITL approval cards from append-only trace chunks."""

    response_by_id = {
        str(response.id): response
        for response in responses
        if response.role == "assistant" and response.tool_calls
    }
    if not response_by_id:
        return

    completed_tool_call_ids = _completed_tool_call_ids(responses)
    result = await db.execute(
        select(MessageEvent)
        .where(MessageEvent.conversation_id == conversation_id)
        .order_by(MessageEvent.created_at)
    )
    # BE-P1: this runs on every GET /messages poll — filter to qualifying
    # records first, then load all their chunks in ONE batched query instead
    # of one query per MessageEvent row (was 1+N on long conversations).
    records = [
        record
        for record in result.scalars().all()
        if any(mid in response_by_id for mid in (record.linked_message_ids or []))
    ]
    if not records:
        return
    events_by_record = await trace_storage.load_events_many(db, records)
    for record in records:
        linked_ids = record.linked_message_ids or []
        target_responses = [response_by_id[mid] for mid in linked_ids if mid in response_by_id]
        events = events_by_record.get(record.id, [])
        payload = _latest_interrupt_payload(events)
        if payload is None:
            continue
        for response in target_responses:
            if not _interrupt_payload_targets_tool_calls(payload, response.tool_calls or []):
                continue
            if _all_interrupt_actions_completed(
                payload,
                response.tool_calls or [],
                completed_tool_call_ids,
            ):
                continue
            response.tool_calls = _merge_interrupt_tool_calls(response.tool_calls or [], payload)
