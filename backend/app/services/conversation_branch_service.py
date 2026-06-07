from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any, NamedTuple

from app.agent_runtime.executor import AgentConfig

REGENERATE_PREVIOUS_ANSWER_LIMIT = 1200


class BranchCheckpointResolution(NamedTuple):
    found: bool
    checkpoint_id: str | None


def with_regeneration_guidance(cfg: AgentConfig, target_msg: Any) -> AgentConfig:
    """Make retry visibly useful without polluting persisted thread messages."""

    from app.agent_runtime.message_utils import content_to_text

    previous_answer = content_to_text(getattr(target_msg, "content", "")).strip()
    if len(previous_answer) > REGENERATE_PREVIOUS_ANSWER_LIMIT:
        previous_answer = previous_answer[:REGENERATE_PREVIOUS_ANSWER_LIMIT].rstrip() + "\n..."

    guidance = (
        "\n\n## 재생성 요청\n"
        "사용자가 방금 assistant 답변 재생성을 요청했습니다. 같은 사용자 메시지에 대해 "
        "정확성은 유지하되, 이전 답변과 다른 표현, 구조, 관점의 대안 답변을 작성하세요. "
        "이전 답변을 그대로 반복하거나 문장 구조를 거의 복사하지 마세요."
    )
    if previous_answer:
        guidance += f"\n\n### 이전 assistant 답변\n{previous_answer}"
    return replace(cfg, system_prompt=f"{cfg.system_prompt}{guidance}")


async def resolve_branch_checkpoint(
    conversation_id: uuid.UUID,
    target_message_id: uuid.UUID,
    *,
    checkpoints: list | None = None,
) -> BranchCheckpointResolution:
    """Translate a frontend-exposed message UUID to a LangGraph fork checkpoint."""

    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.message_utils import parse_msg_id
    from app.services.thread_branch_service import (
        _collect_checkpoints,  # noqa: PLC2701 - controlled router/service bridge
        rewind_to_checkpoint_before_message,
    )

    checkpointer = get_checkpointer()
    if checkpoints is None:
        checkpoints = await _collect_checkpoints(checkpointer, str(conversation_id))
    target_uuid_str = str(target_message_id)
    raw_id: str | None = None
    for ck in checkpoints:
        for idx, msg in enumerate(ck.messages):
            raw = getattr(msg, "id", None)
            if str(parse_msg_id(raw, conversation_id, idx)) == target_uuid_str:
                raw_id = str(raw or f"synthetic-{idx}")
                break
        if raw_id is not None:
            break
    if raw_id is None:
        return BranchCheckpointResolution(found=False, checkpoint_id=None)
    checkpoint_id = await rewind_to_checkpoint_before_message(
        checkpointer, str(conversation_id), raw_id
    )
    return BranchCheckpointResolution(found=True, checkpoint_id=checkpoint_id)


def find_message_in_checkpoints(
    checkpoints: list,
    conversation_id: uuid.UUID,
    target_message_id: uuid.UUID,
) -> tuple[Any, int] | None:
    """Find a frontend-exposed message id across every branch checkpoint."""

    from app.agent_runtime.message_utils import parse_msg_id

    target_uuid_str = str(target_message_id)
    for ck in checkpoints:
        for idx, msg in enumerate(ck.messages):
            raw = getattr(msg, "id", None)
            if str(parse_msg_id(raw, conversation_id, idx)) == target_uuid_str:
                return msg, idx
    return None


def active_checkpoint_from_override(checkpoints: list, checkpoint_id: str | None) -> Any:
    """Resolve the conversation's active branch checkpoint to a concrete leaf."""

    from app.services.thread_branch_service import _build_tree_from_checkpoints  # noqa: PLC2701

    tree = _build_tree_from_checkpoints(
        checkpoints,
        active_checkpoint_id=checkpoint_id,
    )
    active_id = tree.active_checkpoint_id
    if active_id is not None:
        for ck in checkpoints:
            if ck.checkpoint_id == active_id:
                return ck
    return checkpoints[0]
