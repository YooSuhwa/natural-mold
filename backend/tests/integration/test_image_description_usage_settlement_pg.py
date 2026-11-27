"""Measured image-model charges survive access changes without granting caption access."""

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.authz.org_context import OrgContext
from app.config import settings
from app.database import ShieldedAsyncSession, tenant_session
from app.models.conversation_run import ConversationRun
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_model import DailySpendModel
from app.models.daily_spend_user import DailySpendUser
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.organization import OrganizationMember
from app.models.token_usage import TokenUsage
from app.services.chat.image_description_receipts import finish_description, reserve_description
from app.services.chat.image_description_usage import (
    ImageDescriptionBilling,
    settle_image_description_usage,
)
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.integration import _seed
from tests.test_chat_image_description_receipts import seed_receipt_request

pytestmark = pytest.mark.integration


@pytest.fixture
def usage_lab() -> Iterator[RlsLab]:
    with rls_lab() as lab:
        yield lab


@pytest.mark.parametrize("change", ["delete_upload", "revoke_membership"])
async def test_measured_charge_is_scoped_and_posts_once_after_access_changes(
    usage_lab: RlsLab,
    monkeypatch: pytest.MonkeyPatch,
    change: Literal["delete_upload", "revoke_membership"],
) -> None:
    admin_engine = create_async_engine(async_url(usage_lab.admin_url))
    app_engine = create_async_engine(async_url(usage_lab.app_url))
    admin = async_sessionmaker(admin_engine, expire_on_commit=False)
    app = async_sessionmaker(app_engine, class_=ShieldedAsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(_seed, "TestSession", admin)
    try:
        conversation_id = await _seed.seed_conversation_with_agent()
        async with admin() as db:
            request = await seed_receipt_request(db, conversation_id)
            model = Model(provider="openai", model_name="metered-vision", display_name="Auxiliary")
            db.add(model)
            await db.flush()
            request["model_id"] = model.id
            await db.commit()
        context: OrgContext = request["context"]
        run_id: UUID = request["run_id"]
        billing = ImageDescriptionBilling(
            model_name="metered-vision",
            credential_scope="tenant",
            input_rate=Decimal("0.000002"),
            output_rate=Decimal("0.000010"),
        )
        request["billing"] = billing
        async with tenant_session(context, maker=app) as db:
            claim = await reserve_description(db, **request)
            assert claim.status == "acquired"
            receipt_id = claim.receipt_id
            settlement_token = claim.settlement_token
            assert receipt_id is not None and settlement_token is not None
            # Polling clients know the receipt id, never the settlement capability.
            pending = await reserve_description(db, **request)
            assert pending.status == "pending" and pending.settlement_token is None

        if change == "delete_upload":
            async with tenant_session(context, maker=app) as db:
                await db.execute(
                    delete(MessageAttachment).where(
                        MessageAttachment.id == request["attachment_id"]
                    )
                )
                await db.commit()
        else:
            async with admin() as db:
                await db.execute(
                    delete(OrganizationMember).where(
                        OrganizationMember.org_id == context.org_id,
                        OrganizationMember.user_id == context.user_id,
                    )
                )
                await db.commit()

        async def post(
            actor: OrgContext,
            token: str,
            run: UUID,
            barrier: asyncio.Barrier | None = None,
        ) -> bool:
            async with tenant_session(actor, maker=app) as db:
                if barrier is not None:
                    await db.connection()
                    await barrier.wait()
                return await settle_image_description_usage(
                    db,
                    context=actor,
                    run_id=run,
                    receipt_id=receipt_id,
                    settlement_token=token,
                    input_tokens=50,
                    output_tokens=4,
                )

        foreign = replace(context, tenant_id=usage_lab.tenant_b)
        assert not await post(foreign, settlement_token, run_id)
        assert not await post(replace(context, org_id=uuid4()), settlement_token, run_id)
        assert not await post(replace(context, user_id=uuid4()), settlement_token, run_id)
        assert not await post(context, settlement_token, uuid4())
        assert not await post(context, "not-the-issued-capability", run_id)
        async with admin() as db:
            assert await db.get(TokenUsage, receipt_id) is None

        barrier = asyncio.Barrier(2)
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                post(context, settlement_token, run_id, barrier),
                post(context, settlement_token, run_id, barrier),
            ),
            timeout=15,
        )
        assert sorted(outcomes) == [False, True]
        async with tenant_session(context, maker=app) as db:
            assert not await finish_description(
                db,
                context=context,
                run_id=run_id,
                receipt_id=receipt_id,
                description="must not be published",
            )
        async with tenant_session(foreign, maker=app) as db:
            assert await db.get(TokenUsage, receipt_id) is None
        async with admin() as db:
            usage = (
                await db.scalars(
                    select(TokenUsage).where(TokenUsage.conversation_id == conversation_id)
                )
            ).one()
            assert usage.id == receipt_id
            assert usage.run_id is None and usage.auxiliary_run_id == run_id
            assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (50, 4, 54)
            assert usage.estimated_cost == Decimal("0.000140")
            assert usage.model_name == "metered-vision"
            assert usage.llm_credential_scope == "tenant"
            assert (usage.tenant_id, usage.org_id) == (context.tenant_id, context.org_id)
            user_spend = (await db.scalars(select(DailySpendUser))).one()
            agent_spend = (await db.scalars(select(DailySpendAgent))).one()
            model_spend = (await db.scalars(select(DailySpendModel))).one()
            assert user_spend.user_id == context.user_id
            assert agent_spend.agent_id == usage.agent_id
            assert model_spend.model_id == request["model_id"]
            for spend in (user_spend, agent_spend, model_spend):
                assert (spend.total_tokens_in, spend.total_tokens_out) == (50, 4)
                assert spend.total_cost_usd == Decimal("0.000140")
                assert spend.request_count == 1
                assert (spend.tenant_id, spend.org_id) == (context.tenant_id, context.org_id)
            await db.execute(delete(ConversationRun).where(ConversationRun.id == run_id))
            await db.commit()
        async with admin() as db:
            assert await db.get(TokenUsage, receipt_id) is None
    finally:
        await app_engine.dispose()
        await admin_engine.dispose()
