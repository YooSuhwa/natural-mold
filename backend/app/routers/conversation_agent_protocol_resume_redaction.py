from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_interrupts import ThreadInterrupt
from app.routers.conversation_agent_protocol_resume import ResumePayload
from app.services.conversation_resume_redaction import (
    REDACTED_PLACEHOLDER as REDACTED_PLACEHOLDER,
)
from app.services.conversation_resume_redaction import (
    RedactedResumeArgsUnavailable as RedactedResumeArgsUnavailable,
)
from app.services.conversation_resume_redaction import (
    action_requests as _action_requests,
)
from app.services.conversation_resume_redaction import (
    restore_redacted_response as _restore_redacted_response,
)


async def restore_redacted_resume_payload(
    *,
    conversation: Conversation,
    resume: ResumePayload,
    pending_interrupts: list[ThreadInterrupt],
    db: AsyncSession,
    context: OrgContext,
) -> Any:
    if not _resume_contains_edit(resume.input_payload):
        return resume.input_payload

    raw_actions = await _raw_pending_actions_by_interrupt(
        conversation, pending_interrupts, db=db, context=context
    )
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
    *,
    db: AsyncSession,
    context: OrgContext,
) -> dict[str, list[dict[str, Any]]]:
    raw_tool_calls = await _raw_latest_tool_calls(conversation, db=db, context=context)
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


async def _raw_latest_tool_calls(
    conversation: Conversation, *, db: AsyncSession, context: OrgContext
) -> list[dict[str, Any]]:
    from app.agent_runtime.conversation_checkpointer import (
        conversation_checkpointer,
        deferred_saver,
    )
    from app.services.thread_branch_service import build_message_tree

    # Gate outside the try (a foreign thread is a 404, not a failed restore); the
    # saver itself still fails inside it, as before.
    saver = await conversation_checkpointer(
        str(conversation.id), context, db=db, saver=deferred_saver
    )
    try:
        tree = await build_message_tree(
            saver(),
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
