from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.mcp_app_projection import (
    extract_mcp_app_candidates,
    project_verified_mcp_apps,
    verified_mcp_app_artifacts,
)
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.schemas.conversation_history import JsonValue
from app.schemas.conversation_history_protocol import (
    HistoryRequest,
    ThreadHistoryCursor,
    ThreadStateResponse,
    ThreadStateResponseInput,
    build_state_response,
)
from app.services.chat.secrets import collect_conversation_secret_values
from app.services.conversation_protocol_projection import (
    MessageProjectionRequest,
    project_todo_policy_values,
    serialize_langchain_message,
)
from app.services.thread_branch_service import _CheckpointSlim, _collect_checkpoints

_JSON_MAPPING = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class LiveThreadHistoryRequest:
    conversation: Conversation
    history: HistoryRequest
    context: OrgContext
    db: AsyncSession | None = None
    user: CurrentUser | None = None
    checkpointer_factory: Callable[[], BaseCheckpointSaver[str]] = get_checkpointer


@dataclass(frozen=True, slots=True)
class HistoryCheckpointProjectionRequest:
    conversation: Conversation
    checkpoint: _CheckpointSlim
    checkpoint_by_message_id: dict[str, str]
    secret_values: Sequence[str] | None
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None


async def load_live_thread_history(
    request: LiveThreadHistoryRequest,
) -> list[ThreadStateResponse]:
    if request.conversation.agent_lifecycle_state == "purged":
        raise ValueError("purged conversations require retained checkpoint history")
    try:
        checkpointer = request.checkpointer_factory()
    except RuntimeError:
        return []
    secrets: tuple[str, ...] | None = None
    if request.db is not None and request.user is not None:
        secrets = tuple(
            await collect_conversation_secret_values(
                request.db,
                request.conversation,
                request.context,
            )
        )
    checkpoints = await _collect_checkpoints(checkpointer, str(request.conversation.id))
    checkpoint_by_message_id = _checkpoint_map(checkpoints)
    page = _page_checkpoints(
        checkpoints,
        request.history.before,
        request.history.limit,
    )
    return [
        await build_checkpoint_history_response(
            HistoryCheckpointProjectionRequest(
                request.conversation,
                checkpoint,
                checkpoint_by_message_id,
                secrets,
            )
        )
        for checkpoint in page
    ]


def _page_checkpoints(
    checkpoints: Sequence[_CheckpointSlim],
    before: ThreadHistoryCursor | None,
    limit: int,
) -> list[_CheckpointSlim]:
    before_id = _cursor_checkpoint_id(before)
    page: list[_CheckpointSlim] = []
    past_before = before_id is None
    for checkpoint in checkpoints:
        if not past_before:
            if checkpoint.checkpoint_id == before_id:
                past_before = True
            continue
        page.append(checkpoint)
        if len(page) >= limit:
            break
    return page


def _cursor_checkpoint_id(before: ThreadHistoryCursor | None) -> str | None:
    if before is None:
        return None
    if before.checkpoint_id:
        return before.checkpoint_id
    configurable = before.configurable
    return configurable.checkpoint_id if configurable is not None else None


def _checkpoint_map(checkpoints: Sequence[_CheckpointSlim]) -> dict[str, str]:
    result: dict[str, str] = {}
    for checkpoint in sorted(checkpoints, key=lambda item: len(item.messages)):
        for message in checkpoint.messages:
            message_id = message.id
            if isinstance(message_id, str) and message_id and message_id not in result:
                result[message_id] = checkpoint.checkpoint_id
    return result


async def build_checkpoint_history_response(
    request: HistoryCheckpointProjectionRequest,
) -> ThreadStateResponse:
    messages: list[dict[str, JsonValue]] = []
    verified_apps = await verified_mcp_app_artifacts(
        request.checkpoint.messages,
        expected_conversation_id=str(request.conversation.id),
        session_factory=request.session_factory,
    )
    candidate_ids = {
        item.tool_call_id for item in extract_mcp_app_candidates(request.checkpoint.messages)
    }
    for message in request.checkpoint.messages:
        message_id = message.id
        introduced_by = (
            request.checkpoint_by_message_id.get(message_id)
            if isinstance(message_id, str) and message_id
            else request.checkpoint.checkpoint_id
        )
        projected = project_verified_mcp_apps(
            serialize_langchain_message(
                MessageProjectionRequest(
                    message,
                    introduced_by or request.checkpoint.checkpoint_id,
                    secret_values=request.secret_values,
                )
            ),
            verified_apps,
            untrusted_tool_call_ids=candidate_ids,
        )
        messages.append(_JSON_MAPPING.validate_python(projected, strict=True))
    message_values: list[JsonValue] = list(messages)
    values: dict[str, JsonValue] = {"messages": message_values}
    return build_state_response(
        request.conversation,
        ThreadStateResponseInput(
            values=project_todo_policy_values(request.conversation, values),
            checkpoint_id=request.checkpoint.checkpoint_id,
            checkpoint_by_message_id=request.checkpoint_by_message_id,
            metadata_source="moldy_checkpointer_history",
        ),
    )


__all__ = [
    "HistoryCheckpointProjectionRequest",
    "LiveThreadHistoryRequest",
    "build_checkpoint_history_response",
    "load_live_thread_history",
]
