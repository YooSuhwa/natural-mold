from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.message_utils import parse_msg_id
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.agent_runtime.run_secrets import collect_cfg_secret_values
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_checkpoint_state import (
    load_checkpoint_channel_values,
)
from app.routers.conversation_agent_protocol_legacy import legacy_state_messages
from app.services.thread_branch_service import _collect_checkpoints, build_message_tree

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThreadStateSnapshot:
    values: dict[str, Any]
    checkpoint_by_message_id: dict[str, str]
    parent_checkpoint_by_message_id: dict[str, str]


def serialize_langchain_message(
    message: Any,
    *,
    checkpoint_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    secret_values: Iterable[str] | None = None,
) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        dumped = message.model_dump(mode="json")
        payload = dumped if isinstance(dumped, dict) else {"type": "unknown", "content": dumped}
    else:
        get_type = getattr(message, "_getType", lambda: "unknown")
        payload = {
            "type": str(getattr(message, "type", None) or get_type()),
            "content": getattr(message, "content", ""),
            "id": getattr(message, "id", None),
        }
    # ADR-021 C2 — this runs in a plain HTTP GET (no active run / ContextVar),
    # so the run secret set is passed explicitly by the router.
    payload = redact_protocol_data("messages", payload, secret_values=secret_values)
    if not isinstance(payload, dict):
        payload = {"type": "unknown", "content": payload}
    if checkpoint_id is None and not metadata:
        return payload
    return _with_checkpoint_metadata(payload, checkpoint_id, metadata=metadata)


async def collect_state_secret_values(
    db: AsyncSession,
    conversation: Conversation,
    user: CurrentUser,
) -> set[str]:
    """ADR-021 C2 — gather the run's plaintext secrets for state-API egress.

    State / history endpoints are plain HTTP GETs that run *outside* any agent
    run, so the run-scoped redaction ContextVar is never set. We rebuild the
    eager secret set from the same source as a live run (``resolve_agent_context``
    → ``collect_cfg_secret_values``) and hand it to ``redact_protocol_data``
    explicitly. Best-effort: any failure (agent without model, missing
    credential) degrades to heuristics-only rather than blocking the read.
    """

    from app.services.conversation_stream_service import resolve_agent_context

    try:
        cfg = await resolve_agent_context(db, conversation.id, user)
    except Exception:  # noqa: BLE001 — state read must not fail on secret-collect
        logger.debug(
            "state secret collection skipped for conversation %s (heuristics only)",
            conversation.id,
            exc_info=True,
        )
        return set()
    return collect_cfg_secret_values(cfg)


async def load_thread_state_snapshot(
    conversation: Conversation,
    db: AsyncSession | None = None,
    *,
    secret_values: Iterable[str] | None = None,
) -> ThreadStateSnapshot:
    # Materialise once — the set is consumed for the values payload plus every
    # message below; a one-shot generator would be exhausted after the first.
    secrets = tuple(secret_values) if secret_values is not None else None
    try:
        checkpointer = get_checkpointer()
        tree = await build_message_tree(
            checkpointer,
            str(conversation.id),
            active_checkpoint_id=conversation.active_branch_checkpoint_id,
        )
        (
            checkpoint_by_message_id,
            parent_checkpoint_by_message_id,
        ) = await _checkpoint_maps_by_message_id(checkpointer, str(conversation.id), conversation)
    except RuntimeError:
        return await _legacy_thread_state_snapshot(conversation, db)
    if not tree.nodes:
        return await _legacy_thread_state_snapshot(conversation, db)

    values = redact_protocol_data(
        "values",
        await load_checkpoint_channel_values(
            checkpointer,
            thread_id=str(conversation.id),
            checkpoint_id=tree.active_checkpoint_id,
        ),
        secret_values=secrets,
    )
    if not isinstance(values, dict):
        values = {}
    messages: list[dict[str, Any]] = []
    for idx, node in enumerate(tree.nodes):
        aliases = _message_id_aliases(node.message, conversation, idx)
        message_id = aliases[0] if aliases else None
        branch_metadata = _branch_metadata(tree.branches_by_message, node, conversation, idx)
        checkpoint_id = (
            node.introduced_by_checkpoint_id
            if branch_metadata
            else (
                checkpoint_by_message_id.get(message_id)
                if message_id is not None
                else node.introduced_by_checkpoint_id
            )
        )
        checkpoint_id = checkpoint_id or node.introduced_by_checkpoint_id
        payload = serialize_langchain_message(
            node.message,
            checkpoint_id=checkpoint_id,
            metadata=branch_metadata,
            secret_values=secrets,
        )
        if message_id is not None:
            payload = {**payload, "id": message_id}
        messages.append(payload)
        for alias in aliases:
            checkpoint_by_message_id.setdefault(alias, checkpoint_id)

    return ThreadStateSnapshot(
        values={**values, "messages": messages},
        checkpoint_by_message_id=checkpoint_by_message_id,
        parent_checkpoint_by_message_id=parent_checkpoint_by_message_id,
    )


async def _legacy_thread_state_snapshot(
    conversation: Conversation,
    db: AsyncSession | None,
) -> ThreadStateSnapshot:
    if db is None:
        return ThreadStateSnapshot(
            values={},
            checkpoint_by_message_id={},
            parent_checkpoint_by_message_id={},
        )
    messages = await legacy_state_messages(db, conversation.id)
    return ThreadStateSnapshot(
        values={"messages": messages},
        checkpoint_by_message_id={},
        parent_checkpoint_by_message_id={},
    )


def _with_checkpoint_metadata(
    payload: dict[str, Any],
    checkpoint_id: str | None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    additional_kwargs = payload.get("additional_kwargs")
    additional_kwargs_dict = (
        dict(additional_kwargs) if isinstance(additional_kwargs, Mapping) else {}
    )
    existing_metadata = additional_kwargs_dict.get("metadata")
    metadata_dict = dict(existing_metadata) if isinstance(existing_metadata, Mapping) else {}
    extra_metadata = dict(metadata or {})
    if checkpoint_id is not None:
        extra_metadata["checkpoint_id"] = checkpoint_id
    return {
        **payload,
        "additional_kwargs": {
            **additional_kwargs_dict,
            "metadata": {
                **metadata_dict,
                **extra_metadata,
            },
        },
    }


async def _checkpoint_maps_by_message_id(
    checkpointer: Any,
    thread_id: str,
    conversation: Conversation,
) -> tuple[dict[str, str], dict[str, str]]:
    checkpoints = await _collect_checkpoints(checkpointer, thread_id)
    checkpoint_by_message_id: dict[str, str] = {}
    parent_checkpoint_by_message_id: dict[str, str] = {}
    for checkpoint in sorted(
        checkpoints,
        key=lambda item: (len(item.messages), item.checkpoint_id),
    ):
        for idx, message in enumerate(checkpoint.messages):
            aliases = _message_id_aliases(message, conversation, idx)
            if not aliases or aliases[0] in checkpoint_by_message_id:
                continue
            for alias in aliases:
                checkpoint_by_message_id[alias] = checkpoint.checkpoint_id
                if checkpoint.parent_checkpoint_id is not None:
                    parent_checkpoint_by_message_id[alias] = checkpoint.parent_checkpoint_id
    return checkpoint_by_message_id, parent_checkpoint_by_message_id


def _message_id_from_message(message: Any) -> str | None:
    value = getattr(message, "id", None)
    return value if isinstance(value, str) and value else None


def _message_id_aliases(message: Any, conversation: Conversation, idx: int) -> tuple[str, ...]:
    raw = _message_id_from_message(message)
    if raw is None:
        return (str(parse_msg_id(None, conversation.id, idx)),)
    ui_id = str(parse_msg_id(raw, conversation.id, idx))
    return (raw,) if raw == ui_id else (raw, ui_id)


def _ui_message_id(
    raw_id: str,
    conversation: Conversation,
    idx: int,
    *,
    checkpoint_id: str | None = None,
) -> str:
    normalized = (
        f"{raw_id}:{checkpoint_id}" if raw_id.startswith("synthetic-") and checkpoint_id else raw_id
    )
    if raw_id.startswith("synthetic-") and checkpoint_id is None:
        normalized = None
    return str(parse_msg_id(normalized, conversation.id, idx))


def _branch_metadata(
    branches_by_message: Mapping[str, Any],
    node: Any,
    conversation: Conversation,
    idx: int,
) -> dict[str, Any]:
    raw_id = _message_id_from_message(node.message) or f"synthetic-{idx}"
    siblings = list(branches_by_message.get(raw_id, []))
    if not siblings or node.branch_index is None or node.branch_total is None:
        return {}
    return {
        "branches": [
            _ui_message_id(
                sibling.message_id,
                conversation,
                idx,
                checkpoint_id=sibling.checkpoint_id,
            )
            for sibling in siblings
        ],
        "siblingCheckpointIds": [sibling.checkpoint_id for sibling in siblings],
        "activeBranchId": _ui_message_id(
            raw_id,
            conversation,
            idx,
            checkpoint_id=node.introduced_by_checkpoint_id,
        ),
        "branchCheckpointId": node.introduced_by_checkpoint_id,
        "branchIndex": node.branch_index,
        "branchTotal": node.branch_total,
    }
