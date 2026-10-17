"""Assistant v2 서비스 — 대화 관리, SSE 스트리밍."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.assistant.assistant_agent import build_assistant_agent
from app.agent_runtime.streaming import format_sse, stream_agent_response
from app.authz.org_context import OrgContext
from app.schemas.conversation import Decision
from app.services.spend_writer import SpendEntry, spend_queue
from app.services.system_credential_resolver import SystemModelNotConfiguredError
from app.tenancy.checkpoint_audit import checkpoint_tenant

logger = logging.getLogger(__name__)


async def stream_assistant_message(
    db: AsyncSession,
    agent_id: uuid.UUID,
    context: OrgContext,
    thread_id: str,
    user_message: str,
) -> AsyncGenerator[str, None]:
    """Assistant 메시지를 SSE 스트리밍으로 처리한다.

    기존 채팅 인프라(stream_agent_response)를 그대로 재사용한다 (ADR-005 AD-6).
    checkpointer가 히스토리를 자동 관리하므로 별도 세션 테이블 불필요.
    """
    try:
        agent = await build_assistant_agent(db, agent_id, context, thread_id)
    except SystemModelNotConfiguredError as exc:
        # ADR-019: no .env fallback — surface a clear operator-action message
        # instead of crashing the SSE stream.
        logger.warning("Assistant model unconfigured: %s", exc)
        yield format_sse(
            event_names.ERROR,
            {
                "message": (
                    "운영자가 System LLM 설정(텍스트 모델)을 완료해야 "
                    "어시스턴트를 사용할 수 있습니다."
                ),
                "code": "system_model_not_configured",
            },
        )
        return

    messages = [HumanMessage(content=user_message)]
    config = {"configurable": {"thread_id": thread_id, **checkpoint_tenant(context.tenant_id)}}
    usage: dict[str, Any] = {}

    try:
        async for chunk in stream_agent_response(agent, messages, config, usage_sink=usage):
            yield chunk
    finally:  # disconnects and errors still book what the model already used
        book_assistant_usage(context, usage)


async def stream_assistant_resume(
    db: AsyncSession,
    agent_id: uuid.UUID,
    context: OrgContext,
    thread_id: str,
    decisions: Sequence[Decision],
) -> AsyncGenerator[str, None]:
    try:
        agent = await build_assistant_agent(db, agent_id, context, thread_id)
    except SystemModelNotConfiguredError as exc:
        logger.warning("Assistant model unconfigured: %s", exc)
        yield format_sse(
            event_names.ERROR,
            {
                "message": (
                    "운영자가 System LLM 설정(텍스트 모델)을 완료해야 "
                    "어시스턴트를 사용할 수 있습니다."
                ),
                "code": "system_model_not_configured",
            },
        )
        return

    decisions_payload = [decision.model_dump(exclude_none=True) for decision in decisions]
    config = {"configurable": {"thread_id": thread_id, **checkpoint_tenant(context.tenant_id)}}

    usage: dict[str, Any] = {}
    try:
        async for chunk in stream_agent_response(
            agent,
            Command(resume={"decisions": decisions_payload}),
            config,
            usage_sink=usage,
        ):
            yield chunk
    finally:
        book_assistant_usage(context, usage)


def book_assistant_usage(context: OrgContext, usage: Mapping[str, Any]) -> None:
    """F-14 (11.10, Q8): the caller pays for the Assistant, in their organization.

    The Assistant has no run row or agent spend (it edits the agent, it does not run it)
    and the system model carries no price here, so only tokens reach the user axis.
    """
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    if tokens_in == 0 and tokens_out == 0:
        return
    spend_queue.add(
        SpendEntry(
            date=datetime.now(UTC).date(),
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=Decimal("0"),
        )
    )
