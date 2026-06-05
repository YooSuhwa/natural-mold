from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.exceptions import ValidationError
from app.schemas.memory import (
    MAX_MEMORY_CONTENT_LENGTH,
    MAX_MEMORY_REASON_LENGTH,
    MemoryProposalCreate,
    MemoryRecordCreate,
)
from app.services import memory_service

MEMORY_TOOL_NAMES = frozenset({"propose_memory", "save_user_memory", "save_agent_memory"})


class ProposeMemoryInput(BaseModel):
    scope: Literal["user", "agent"] = Field(
        description="Whether this memory should apply to the user globally or this agent only."
    )
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_REASON_LENGTH)

    @field_validator("content")
    @classmethod
    def _clean_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be empty")
        return stripped


class SaveMemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_REASON_LENGTH)

    @field_validator("content")
    @classmethod
    def _clean_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be empty")
        return stripped


SessionFactory = Callable[[], AsyncSession]
_REDACTED_MEMORY_CONTENT = "<redacted>"


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _normalize_optional_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    stripped = reason.strip()
    return stripped or None


def _base_payload(
    *,
    memory_event: str,
    scope: str,
    content: str,
    reason: str | None,
    policy: str,
    agent_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "memory_event": memory_event,
        "scope": scope,
        "content": _REDACTED_MEMORY_CONTENT if memory_event == "memory_rejected" else content,
        "reason": reason,
        "policy": policy,
        "agent_id": str(agent_id) if agent_id else None,
        "conversation_id": str(conversation_id) if conversation_id else None,
    }


def build_memory_tools(
    *,
    user_id: str,
    agent_id: str | None,
    conversation_id: str | None,
    is_trigger_mode: bool,
    session_factory: SessionFactory = async_session,
) -> list[BaseTool]:
    """Build policy-bound long-term memory tools for a Moldy agent run."""

    user_uuid = _parse_uuid(user_id)
    agent_uuid = _parse_uuid(agent_id)
    conversation_uuid = _parse_uuid(conversation_id)
    if user_uuid is None:
        return []

    async def apply_memory_request(
        *,
        scope: Literal["user", "agent"],
        content: str,
        reason: str | None,
    ) -> str:
        normalized_reason = _normalize_optional_reason(reason)
        async with session_factory() as db:
            policy = await memory_service.resolve_effective_policy(
                db,
                user_id=user_uuid,
                agent_id=agent_uuid,
            )
            write_policy = policy.trigger_write_policy if is_trigger_mode else policy.write_policy
            if policy.allowed_scopes != "both" and policy.allowed_scopes != scope:
                return _json(
                    _base_payload(
                        memory_event="memory_rejected",
                        scope=scope,
                        content=content,
                        reason="scope_not_allowed",
                        policy=write_policy,
                        agent_id=agent_uuid,
                        conversation_id=conversation_uuid,
                    )
                )
            if write_policy == "off":
                return _json(
                    _base_payload(
                        memory_event="memory_rejected",
                        scope=scope,
                        content=content,
                        reason="memory_write_disabled",
                        policy=write_policy,
                        agent_id=agent_uuid,
                        conversation_id=conversation_uuid,
                    )
                )
            if write_policy == "ask":
                try:
                    proposal = await memory_service.create_memory_proposal(
                        db,
                        user_id=user_uuid,
                        payload=MemoryProposalCreate(
                            scope=scope,
                            content=content,
                            reason=normalized_reason,
                            agent_id=agent_uuid,
                            conversation_id=conversation_uuid,
                        ),
                    )
                except ValidationError as exc:
                    await db.rollback()
                    return _json(
                        _base_payload(
                            memory_event="memory_rejected",
                            scope=scope,
                            content=content,
                            reason=exc.code,
                            policy=write_policy,
                            agent_id=agent_uuid,
                            conversation_id=conversation_uuid,
                        )
                    )
                if proposal is None:
                    return _json(
                        _base_payload(
                            memory_event="memory_rejected",
                            scope=scope,
                            content=content,
                            reason="agent_not_found",
                            policy=write_policy,
                            agent_id=agent_uuid,
                            conversation_id=conversation_uuid,
                        )
                    )
                payload = _base_payload(
                    memory_event="memory_proposed",
                    scope=scope,
                    content=proposal.content,
                    reason=proposal.reason,
                    policy=write_policy,
                    agent_id=proposal.agent_id,
                    conversation_id=proposal.conversation_id,
                )
                payload["id"] = str(proposal.id)
                return _json(payload)

            try:
                record = await memory_service.create_memory_record(
                    db,
                    user_id=user_uuid,
                    payload=MemoryRecordCreate(
                        scope=scope,
                        content=content,
                        reason=normalized_reason,
                        agent_id=agent_uuid,
                        source_conversation_id=conversation_uuid,
                    ),
                )
            except ValidationError as exc:
                await db.rollback()
                return _json(
                    _base_payload(
                        memory_event="memory_rejected",
                        scope=scope,
                        content=content,
                        reason=exc.code,
                        policy=write_policy,
                        agent_id=agent_uuid,
                        conversation_id=conversation_uuid,
                    )
                )
            if record is None:
                return _json(
                    _base_payload(
                        memory_event="memory_rejected",
                        scope=scope,
                        content=content,
                        reason="agent_not_found",
                        policy=write_policy,
                        agent_id=agent_uuid,
                        conversation_id=conversation_uuid,
                    )
                )
            payload = _base_payload(
                memory_event="memory_saved",
                scope=scope,
                content=record.content,
                reason=record.reason,
                policy=write_policy,
                agent_id=record.agent_id,
                conversation_id=record.source_conversation_id,
            )
            payload["id"] = str(record.id)
            return _json(payload)

    async def propose_memory(
        scope: Literal["user", "agent"],
        content: str,
        reason: str | None = None,
    ) -> str:
        """Propose a long-term memory for user review according to policy."""

        return await apply_memory_request(scope=scope, content=content, reason=reason)

    async def save_user_memory(content: str, reason: str | None = None) -> str:
        """Save or propose a user-level long-term memory according to policy."""

        return await apply_memory_request(scope="user", content=content, reason=reason)

    async def save_agent_memory(content: str, reason: str | None = None) -> str:
        """Save or propose an agent-specific long-term memory according to policy."""

        return await apply_memory_request(scope="agent", content=content, reason=reason)

    return [
        StructuredTool.from_function(
            coroutine=propose_memory,
            name="propose_memory",
            description=(
                "Use when a durable memory may be useful. The server applies the user's "
                "memory policy; in ask mode this creates a review card instead of saving."
            ),
            args_schema=ProposeMemoryInput,
        ),
        StructuredTool.from_function(
            coroutine=save_user_memory,
            name="save_user_memory",
            description=(
                "Save a memory that should apply across the user's agents. The server may "
                "degrade this to a proposal when policy requires approval."
            ),
            args_schema=SaveMemoryInput,
        ),
        StructuredTool.from_function(
            coroutine=save_agent_memory,
            name="save_agent_memory",
            description=(
                "Save a memory that should apply only to this agent. The server may degrade "
                "this to a proposal when policy requires approval."
            ),
            args_schema=SaveMemoryInput,
        ),
    ]
