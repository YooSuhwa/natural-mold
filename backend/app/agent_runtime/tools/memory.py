from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.memory_paths import state_has_outside_content
from app.agent_runtime.tools.agent_memo import agent_memo_tools
from app.authz.org_context import OrgContext
from app.database import async_session, tenant_session
from app.exceptions import ValidationError
from app.models.conversation import Conversation
from app.schemas.memory import (
    MAX_MEMORY_CONTENT_LENGTH,
    MAX_MEMORY_REASON_LENGTH,
)
from app.services import memory_service

MEMORY_TOOL_NAMES = frozenset({"propose_memory", "save_user_memory", "save_agent_memory"})
TOOL_KINDS = frozenset({"fact", "preference", "commitment", "experience"})


class _MemoryShapeInput(BaseModel):
    kind: Literal["fact", "preference", "commitment", "experience"] = Field(
        default="fact",
        description=(
            "fact, preference, commitment (a decision or promise) or experience (something "
            "that happened)."
        ),
    )
    topic: str | None = Field(
        default=None,
        max_length=120,
        description="A short subject key such as a project, tool or company name.",
    )
    # Plan 15 (5.3-1): the run's messages, injected by the tool node (never by the model),
    # so the server can tell whether outside content reached the conversation.
    state: Annotated[dict | None, InjectedState] = None


class ProposeMemoryInput(_MemoryShapeInput):
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


class SaveMemoryInput(_MemoryShapeInput):
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
    context: OrgContext,
    agent_id: str | None,
    conversation_id: str | None,
    is_trigger_mode: bool,
    session_factory: SessionFactory | None = None,
    agent_memo_proposals: bool = False,
    run_path: str = "chat",
) -> list[BaseTool]:
    """Build policy-bound long-term memory tools for a Moldy agent run.

    ``agent_memo_proposals`` (F-09): a shared run also gets ``propose_agent_memo``.
    """

    user_uuid = context.user_id
    agent_uuid = _parse_uuid(agent_id)
    conversation_uuid = _parse_uuid(conversation_id)
    if user_uuid is None:
        return []

    async def _origin(db: AsyncSession, state: Mapping[str, Any] | None) -> str:
        """Plan 15 (5.3-1): the server decides; outside content makes it ``tool_derived``."""
        conversation = await db.get(Conversation, conversation_uuid) if conversation_uuid else None
        if conversation is not None and conversation.memory_external_content_at is not None:
            return "tool_derived"
        messages = (state or {}).get("messages") or []
        if state_has_outside_content(messages):
            if conversation is not None:
                conversation.memory_external_content_at = datetime.now(UTC)
            return "tool_derived"
        return "agent_inferred"

    async def apply_memory_request(
        *,
        scope: Literal["user", "agent"],
        content: str,
        reason: str | None,
        state: Mapping[str, Any] | None = None,
        kind: str = "fact",
        topic: str | None = None,
    ) -> str:
        normalized_reason = _normalize_optional_reason(reason)
        trigger = is_trigger_mode or run_path == "trigger"
        path = "trigger" if trigger else run_path
        policy_label = "trigger" if trigger else "chat"

        def payload(event: str, *, reason_code: str | None = None, **extra: Any) -> str:
            body = _base_payload(
                memory_event=event,
                scope=scope,
                content=content,
                reason=reason_code if event == "memory_rejected" else normalized_reason,
                policy=policy_label,
                agent_id=agent_uuid if scope == "agent" else None,
                conversation_id=conversation_uuid,
            )
            body.update(extra)
            return _json(body)

        async with tenant_session(context, maker=session_factory or async_session) as db:
            policy = await memory_service.resolve_effective_policy(
                db, context=context, agent_id=agent_uuid
            )
            if policy.allowed_scopes != "both" and policy.allowed_scopes != scope:
                return payload("memory_rejected", reason_code="scope_not_allowed")
            origin = await _origin(db, state)
            try:
                result = await memory_service.write_memory(
                    db,
                    context=context,
                    write=memory_service.memory_write(
                        scope=scope,
                        content=content,
                        reason=normalized_reason,
                        agent_id=agent_uuid,
                        conversation_id=conversation_uuid,
                        shape=memory_service.MemoryShape(
                            kind=kind if kind in TOOL_KINDS else "fact",
                            topic=(topic or "").strip()[:120] or None,
                            origin=origin,
                        ),
                        run_path=path,
                        channel="tool",
                    ),
                )
            except ValidationError as exc:
                await db.rollback()
                return payload("memory_rejected", reason_code=exc.code)
            if origin == "tool_derived":
                await db.commit()  # keep the conversation's outside-content mark
            if result.kind == "rejected":
                return payload("memory_rejected", reason_code=result.reason)
            if result.kind == "proposed" and result.proposal is not None:
                return payload("memory_proposed", id=str(result.proposal.id), origin=origin)
            record = result.record
            assert record is not None  # noqa: S101 - saved or duplicate carry the record
            return payload(
                "memory_saved",
                id=str(record.id),
                origin=origin,
                duplicate=result.kind == "duplicate",
                review_later=result.review_later,
            )

    async def propose_memory(
        scope: Literal["user", "agent"],
        content: str,
        reason: str | None = None,
        kind: str = "fact",
        topic: str | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> str:
        """Propose a long-term memory for user review according to policy."""

        return await apply_memory_request(
            scope=scope, content=content, reason=reason, state=state, kind=kind, topic=topic
        )

    async def save_user_memory(
        content: str,
        reason: str | None = None,
        kind: str = "fact",
        topic: str | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> str:
        """Save or propose a user-level long-term memory according to policy."""

        return await apply_memory_request(
            scope="user", content=content, reason=reason, state=state, kind=kind, topic=topic
        )

    async def save_agent_memory(
        content: str,
        reason: str | None = None,
        kind: str = "fact",
        topic: str | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> str:
        """Save or propose an agent-specific long-term memory according to policy."""

        return await apply_memory_request(
            scope="agent", content=content, reason=reason, state=state, kind=kind, topic=topic
        )

    memo = agent_memo_proposals and agent_uuid is not None and not is_trigger_mode
    return [
        *agent_memo_tools(
            memo, context, agent_uuid, conversation_uuid, session_factory, service=memory_service
        ),
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
