"""``propose_agent_memo``: a runner of someone else's agent proposes to its shared memo.

F-09 (plan 11.8): such runs cannot write ``/agents/{id}/AGENTS.md``; this tool files a
``memory_proposals`` row (``scope='agent_memo'``) that the agent's editors approve or
reject. The proposer is told the editors will see the text.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.authz.org_context import OrgContext
from app.database import async_session, tenant_session
from app.exceptions import AppError
from app.schemas.memory import MAX_MEMORY_CONTENT_LENGTH, MAX_MEMORY_REASON_LENGTH

TOOL_NAME = "propose_agent_memo"
AUDIENCE = "agent_editors"
Propose = Callable[..., Awaitable[Any]]


class ProposeAgentMemoInput(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_REASON_LENGTH)

    @field_validator("content")
    @classmethod
    def _clean_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be empty")
        return stripped


def _payload(event: str, content: str, reason: str | None, **extra: Any) -> str:
    rejected = event == event_names.MEMORY_REJECTED
    body = {
        "memory_event": event,
        "scope": "agent_memo",
        "content": "<redacted>" if rejected else content,
        "reason": reason,
        "visible_to": AUDIENCE,
        **extra,
    }
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def build_agent_memo_tool(
    *,
    context: OrgContext,
    agent_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    propose: Propose,
    session_factory: Callable[[], AsyncSession] | None = None,
) -> BaseTool:
    conversation = str(conversation_id) if conversation_id else None
    ids = {"agent_id": str(agent_id), "conversation_id": conversation}

    async def propose_agent_memo(content: str, reason: str | None = None) -> str:
        """Propose a note for everyone who uses this agent; its editors review it."""
        note = (reason.strip() or None) if reason else None
        async with tenant_session(context, maker=session_factory or async_session) as db:
            try:
                proposal = await propose(
                    db,
                    context=context,
                    agent_id=agent_id,
                    content=content,
                    reason=note,
                    conversation_id=conversation_id,
                )
            except AppError as exc:  # secret, disabled memo, pending cap, integrity
                await db.rollback()
                return _payload(event_names.MEMORY_REJECTED, content, exc.code, **ids)
            if proposal is None:
                return _payload(event_names.MEMORY_REJECTED, content, "agent_not_found", **ids)
            return _payload(
                event_names.AGENT_MEMO_PROPOSED,
                proposal.content,
                proposal.reason,
                id=str(proposal.id),
                **ids,
            )

    return StructuredTool.from_function(
        coroutine=propose_agent_memo,
        name=TOOL_NAME,
        description=(
            "Use only when something should be remembered for everyone who uses this "
            "agent. It does not save: the agent's editors review the proposal and can "
            "see its text. Personal memories belong in save_agent_memory instead."
        ),
        args_schema=ProposeAgentMemoInput,
    )


def agent_memo_tools(
    enabled: bool,
    context: OrgContext,
    agent_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    session_factory: Callable[[], AsyncSession] | None,
    *,
    service: Any,
) -> list[BaseTool]:
    """``[propose_agent_memo]`` for a shared run, else ``[]``. ``service`` is the memory
    service module; ``create_agent_memo_proposal`` is looked up on it at call time."""
    if not enabled or agent_id is None:
        return []

    async def call(db: AsyncSession, **kwargs: Any) -> Any:
        return await service.create_agent_memo_proposal(db, **kwargs)

    return [
        build_agent_memo_tool(
            context=context,
            agent_id=agent_id,
            conversation_id=conversation_id,
            propose=call,
            session_factory=session_factory,
        )
    ]
