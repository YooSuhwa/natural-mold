"""Token usage records whose key paid, and the company usage view separates company keys
(personal-tenant plan P2, Q2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.authz.conversation_scope import require_conversation_agent_id
from app.models.conversation import Conversation
from app.models.token_usage import TokenUsage
from app.services import conversation_run_service
from app.services.conversation_run_metrics_service import persist_run_metrics_and_usage
from app.services.tenant_usage import tenant_usage
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent
from tests.test_conversation_run_metrics_service import _snapshot


async def _run_with_usage(scope: str | None, completion_tokens: int) -> None:
    conversation_id = await seed_conversation_with_agent(model_name=f"m-{completion_tokens}")
    async with TestSession() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            session,
            conversation_id=conversation_id,
            agent_id=require_conversation_agent_id(conversation),
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="usage",
        )
        await persist_run_metrics_and_usage(
            session,
            run=run,
            snapshot=replace(_snapshot(), completion_tokens=completion_tokens),
            model_name="m",
            llm_credential_scope=scope,
        )
        await session.commit()


@pytest.mark.asyncio
async def test_usage_rows_record_whose_key_paid_and_the_company_view_sums_company_keys() -> None:
    await _run_with_usage("tenant", 7)
    await _run_with_usage("personal", 11)
    await _run_with_usage(None, 13)
    async with TestSession() as session:
        scopes = sorted(
            (row.completion_tokens, row.llm_credential_scope)
            for row in (await session.scalars(select(TokenUsage))).all()
        )
        today = datetime.now(UTC).date()
        summary = await tenant_usage(session, TEST_TENANT_ID, today, today)
    assert scopes == [(7, "tenant"), (11, "personal"), (13, None)]
    assert summary.company_keys.request_count == 1
    assert summary.company_keys.total_tokens_out == 7
