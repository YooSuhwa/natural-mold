"""Token usage persistence and model pricing lookup.

BE-S1 split from ``app.services.chat_service`` — pure move, no behavior
change. The ``_select`` alias is kept from the original deferred import so
the moved body stays byte-identical.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.token_usage import TokenUsage


async def _resolve_agent_model_pricing(
    db: AsyncSession, conversation: Conversation
) -> tuple[float | None, float | None]:
    """W7-4 — conversation.agent.model의 ``cost_per_*_token`` 단가를 조회.

    Decimal → float 변환. Agent/Model row가 사라졌거나 단가가 NULL이면
    ``(None, None)``. 호출자(``langchain_messages_to_response``)는 None을
    받으면 ``estimated_cost``를 채우지 않는다.
    """
    result = await db.execute(
        _select(Model.cost_per_input_token, Model.cost_per_output_token)
        .join(Agent, Agent.model_id == Model.id)
        .where(Agent.id == conversation.agent_id)
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None, None
    cost_in, cost_out = row
    return (
        float(cost_in) if cost_in is not None else None,
        float(cost_out) if cost_out is not None else None,
    )


async def save_token_usage(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost: float | None = None,
) -> TokenUsage:
    usage = TokenUsage(
        conversation_id=conversation_id,
        agent_id=agent_id,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost=estimated_cost,
    )
    db.add(usage)
    await db.commit()
    return usage
