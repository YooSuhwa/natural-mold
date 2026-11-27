"""Durable auxiliary-model receipts never authorize duplicate or cross-scope calls."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_image_description import ChatImageDescription
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.models.organization import OrganizationMember
from app.services.chat.image_description_receipts import (
    find_description,
    finish_description,
    reserve_description,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent

_DIGEST = hashlib.sha256(b"first image bytes").hexdigest()


async def seed_receipt_request(db: AsyncSession, conversation_id: UUID) -> dict[str, Any]:
    conversation = await db.get(Conversation, conversation_id)
    assert conversation
    context = unit_context(conversation.user_id)
    run = ConversationRun(
        id=uuid4(),
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        source="chat",
        status="running",
    )
    attachment = MessageAttachment(
        id=uuid4(),
        conversation_id=conversation.id,
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        mime_type="image/png",
        size_bytes=17,
        filename="image.png",
        storage_path="fixture/image",
        url="/fixture/image",
        content_sha256=_DIGEST,
    )
    db.add_all([run, attachment])
    await db.commit()
    return {
        "context": context,
        "conversation_id": conversation_id,
        "attachment_id": attachment.id,
        "run_id": run.id,
        "content_sha256": _DIGEST,
        "question": "어떤 모양인가요?",
        "model_id": uuid4(),
        "policy_key": "test policy 1",
    }


def _lookup_for(request: dict[str, Any]) -> dict[str, Any]:
    lookup = request.copy()
    del lookup["run_id"]
    lookup["question_sha256"] = hashlib.sha256(lookup.pop("question").encode()).hexdigest()
    return lookup


async def test_claim_survives_restart_and_matches_all_pins() -> None:
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        request = await seed_receipt_request(db, conversation_id)
        claim = await reserve_description(db, **request)
        assert claim.status == "acquired" and claim.receipt_id is not None
    async with TestSession() as db:
        assert (await reserve_description(db, **request)).status == "pending"
        assert not await finish_description(
            db,
            context=request["context"],
            receipt_id=claim.receipt_id,
            run_id=uuid4(),
            description="stale worker",
        )
        assert await finish_description(
            db,
            context=request["context"],
            run_id=request["run_id"],
            receipt_id=claim.receipt_id,
            description="파란 원",
            input_tokens=50,
            output_tokens=4,
            estimated_cost_usd=Decimal("0.00000123"),
            credential_scope="tenant",
        )
        assert not await finish_description(
            db,
            context=request["context"],
            run_id=request["run_id"],
            receipt_id=claim.receipt_id,
            failure_code="timeout",
        )
        row = await db.scalar(select(ChatImageDescription))
        assert row and row.estimated_cost_usd == Decimal("0.00000123")
        assert (row.input_tokens, row.output_tokens, row.credential_scope) == (50, 4, "tenant")
        replay = await reserve_description(db, **request)
        assert replay.status == "completed" and replay.description == "파란 원"
        async with TestSession() as another:
            new_run = (await seed_receipt_request(another, conversation_id))["run_id"]
            recovered = await reserve_description(another, **(request | {"run_id": new_run}))
            assert recovered == replay
        lookup = _lookup_for(request)
        assert await find_description(db, **lookup) == replay
        for changed in (
            {"policy_key": "another policy"},
            {"content_sha256": "0" * 64},
            {"model_id": uuid4()},
            {"question_sha256": "0" * 64},
        ):
            assert (await find_description(db, **(lookup | changed))).status == "unavailable"
        assert (
            await reserve_description(db, **(request | {"content_sha256": "0" * 64}))
        ).status == "unavailable"
        assert (
            await reserve_description(db, **(request | {"question": "색은?"}))
        ).status == "acquired"


async def test_private_read_and_dispatch_fail_closed_after_revocation() -> None:
    async with TestSession() as db:
        request = await seed_receipt_request(db, await seed_conversation_with_agent())
        claim = await reserve_description(db, **request)
        assert claim.receipt_id is not None
        ctx = unit_context(TEST_USER_ID)
        lookup = _lookup_for(request)
        for other in (
            replace(ctx, org_id=uuid4()),
            replace(ctx, tenant_id=uuid4()),
            replace(ctx, user_id=uuid4()),
        ):
            assert (
                await reserve_description(db, **(request | {"context": other}))
            ).status == "unavailable"
            assert (
                await find_description(db, **(lookup | {"context": other}))
            ).status == "unavailable"
        assert await finish_description(
            db,
            context=ctx,
            receipt_id=claim.receipt_id,
            run_id=request["run_id"],
            failure_code="timeout",
        )
        replay = await reserve_description(db, **request)
        assert replay.status == "failed" and replay.failure_code == "timeout"
        await db.execute(
            delete(OrganizationMember).where(OrganizationMember.user_id == ctx.user_id)
        )
        await db.commit()
        assert (await find_description(db, **lookup)).status == "unavailable"
        assert (await reserve_description(db, **request)).status == "unavailable"


async def test_attachment_replacement_invalidates_receipt() -> None:
    async with TestSession() as db:
        request = await seed_receipt_request(db, await seed_conversation_with_agent())
        assert (await reserve_description(db, **request)).status == "acquired"
        await db.execute(
            update(MessageAttachment)
            .where(MessageAttachment.id == request["attachment_id"])
            .values(content_sha256="c" * 64)
        )
        await db.commit()
        assert (await find_description(db, **_lookup_for(request))).status == "unavailable"
        assert (await reserve_description(db, **request)).status == "unavailable"
        assert (
            await reserve_description(db, **(request | {"content_sha256": "c" * 64}))
        ).status == "acquired"


async def test_terminal_run_cannot_claim_an_external_call() -> None:
    async with TestSession() as db:
        request = await seed_receipt_request(db, await seed_conversation_with_agent())
        await db.execute(
            update(ConversationRun)
            .where(ConversationRun.id == request["run_id"])
            .values(status="canceled")
        )
        await db.commit()
        assert (await reserve_description(db, **request)).status == "unavailable"
        assert (await db.scalars(select(ChatImageDescription))).all() == []
