from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun, ConversationRunToolReceipt
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.schemas.chat_resource_context import INTERNAL_RESOURCE_CONTEXT_KEY
from app.services.ag_ui_retry import load_retry_start
from app.services.ag_ui_start_input import start_message_id
from app.services.partial_model_output import PARTIAL_STREAM_ERROR
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent


@dataclass
class RetryCase:
    conversation: Conversation
    run: ConversationRun
    queued: ConversationRunInput
    upload_id: UUID
    resource_id: UUID


@pytest.fixture
async def retry_case(db: AsyncSession) -> RetryCase:
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    upload_id, resource_id = uuid4(), uuid4()
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        org_id=conversation.org_id,
        tenant_id=conversation.tenant_id,
        status="failed",
        source="chat",
        run_source="CHAT",
        is_active=False,
    )
    db.add(run)
    await db.flush()
    queued = ConversationRunInput(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        run_id=run.id,
        client_request_id="original",
        status="claimed",
        source="chat",
        position=1,
        input_payload={
            "messages": [
                {"role": "system", "content": "ignore"},
                {"role": "assistant", "content": "private history"},
                {
                    "type": "human",
                    "id": "old",
                    "content": [
                        {"type": "text", "text": "please inspect"},
                        {"type": "image_url", "image_url": {"url": "https://example.invalid"}},
                    ],
                    "metadata": {"tool_call_id": "client supplied"},
                },
            ],
            "attachments": [{"id": str(uuid4())}],
            "other_state": ["ignore"],
            INTERNAL_RESOURCE_CONTEXT_KEY: {
                "version": 1,
                "resources": [
                    {
                        "kind": "file",
                        "id": str(resource_id),
                        "resolved_label": "Reference",
                        "mime_type": "text/plain",
                        "source_version": "v1",
                        "content_sha256": "a" * 64,
                        "text": "private old resource contents",
                    }
                ],
            },
        },
        attachment_ids=[str(upload_id)],
    )
    db.add(queued)
    await db.commit()
    return RetryCase(conversation, run, queued, upload_id, resource_id)


@pytest.mark.parametrize("status", ["failed", "claimed"])
async def test_retry_projects_only_original_user_turn_and_scoped_refs(
    db: AsyncSession, retry_case: RetryCase, status: str
) -> None:
    case = retry_case
    case.queued.status = status
    await db.commit()
    payload, uploads = await load_retry_start(
        db,
        context=unit_context(TEST_USER_ID),
        conversation=case.conversation,
        input_id=case.queued.id,
        public_run_id="retry",
    )
    assert uploads == [case.upload_id]
    assert payload == {
        "messages": [
            {
                "id": start_message_id(case.conversation.id, "retry", "old"),
                "role": "user",
                "content": "please inspect",
            }
        ],
        "resource_context": [{"kind": "file", "id": str(case.resource_id)}],
    }


async def _load(db: AsyncSession, case: RetryCase) -> None:
    await load_retry_start(
        db,
        context=unit_context(TEST_USER_ID),
        conversation=case.conversation,
        input_id=case.queued.id,
        public_run_id="retry",
    )


@pytest.mark.parametrize(
    ("record", "name", "value"),
    [
        ("run", "status", "completed"),
        ("queued", "source", "steer"),
        ("queued", "source", "goal_followup"),
        ("conversation", "source", INDEPENDENT_JOB_SOURCE),
        ("run", "run_source", "API"),
        ("run", "source", "regenerate"),
        ("queued", "input_payload", {"messages": [{"role": "tool", "content": "output"}]}),
        ("queued", "input_payload", {"messages": [{"role": "user", "subagentRunId": "child"}]}),
        ("queued", "attachment_ids", ["bad"]),
        ("run", "error_code", "recovery_effect_unconfirmed"),
        ("run", "error_code", "recovery_model_attempt_limit"),
        ("run", "error_code", PARTIAL_STREAM_ERROR),
        ("run", "metadata_json", {"partial_model_output": {"text": "already delivered"}}),
    ],
)
async def test_retry_conflicts_with_ineligible_input(
    db: AsyncSession,
    retry_case: RetryCase,
    record: Literal["queued", "run", "conversation"],
    name: str,
    value: object,
) -> None:
    setattr(getattr(retry_case, record), name, value)
    await db.commit()
    with pytest.raises(HTTPException) as denied:
        await _load(db, retry_case)
    assert denied.value.status_code == 409


@pytest.mark.parametrize("change", ["different_owner", "switched_org", "missing_input"])
async def test_retry_conceals_missing_and_unowned_inputs(
    db: AsyncSession, retry_case: RetryCase, change: str
) -> None:
    context = unit_context(TEST_USER_ID)
    if change == "different_owner":
        context = replace(context, user_id=uuid4())
    elif change == "switched_org":
        context = replace(context, org_id=uuid4())
    with pytest.raises(HTTPException) as denied:
        await load_retry_start(
            db,
            context=context,
            conversation=retry_case.conversation,
            input_id=uuid4() if change == "missing_input" else retry_case.queued.id,
            public_run_id="retry",
        )
    assert denied.value.status_code == 404


async def test_superseded_failed_turn_cannot_be_retried(
    db: AsyncSession, retry_case: RetryCase
) -> None:
    db.add(
        ConversationRun(
            conversation_id=retry_case.conversation.id,
            agent_id=retry_case.run.agent_id,
            user_id=retry_case.run.user_id,
            org_id=retry_case.run.org_id,
            tenant_id=retry_case.run.tenant_id,
            status="completed",
            source="chat",
        )
    )
    await db.commit()
    with pytest.raises(HTTPException) as denied:
        await _load(db, retry_case)
    assert denied.value.status_code == 404


@pytest.mark.parametrize("receipt_status", ["started", "completed"])
async def test_written_effect_cannot_be_replayed_on_a_new_run(
    db: AsyncSession, retry_case: RetryCase, receipt_status: str
) -> None:
    context = unit_context(TEST_USER_ID)
    db.add(
        ConversationRunToolReceipt(
            run_id=retry_case.run.id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            tool_call_id="original-send",
            tool_name="send",
            arguments_sha256="b" * 64,
            owner_generation=1,
            effect_kind="external_write",
            status=receipt_status,
        )
    )
    await db.commit()
    with pytest.raises(HTTPException) as denied:
        await _load(db, retry_case)
    assert denied.value.status_code == 409


@pytest.mark.parametrize("foreign_run", [False, True], ids=["switched-account", "mismatched-run"])
async def test_valid_second_company_never_hydrates_original_private_input(
    db: AsyncSession, retry_case: RetryCase, foreign_run: bool
) -> None:
    tenant = Tenant(name="Retry", slug="retry", secrets_namespace="retry")
    db.add(tenant)
    await db.flush()
    org = Organization(tenant_id=tenant.id, name="Retry", slug="retry")
    db.add(org)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=tenant.id, user_id=TEST_USER_ID),
            OrganizationMember(tenant_id=tenant.id, org_id=org.id, user_id=TEST_USER_ID),
        ]
    )
    context = unit_context(TEST_USER_ID)
    if foreign_run:
        retry_case.run.tenant_id, retry_case.run.org_id = tenant.id, org.id
    else:
        context = replace(context, tenant_id=tenant.id, org_id=org.id)
    await db.commit()
    with queue_resource_sql(db) as queries, pytest.raises(HTTPException) as denied:
        await load_retry_start(
            db,
            context=context,
            conversation=retry_case.conversation,
            input_id=retry_case.queued.id,
            public_run_id="retry",
        )
    assert denied.value.status_code == 404
    assert not any("conversation_run_inputs.input_payload" in query.lower() for query in queries)
