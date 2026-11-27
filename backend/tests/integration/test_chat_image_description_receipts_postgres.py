"""Real receipt claims, deletion and tenant isolation under the non-owner PostgreSQL role."""

import asyncio
from collections.abc import Iterator
from dataclasses import replace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import ShieldedAsyncSession, tenant_session
from app.models.chat_image_description import ChatImageDescription
from app.models.message_attachment import MessageAttachment
from app.services.chat.image_description_receipts import (
    DescriptionReservation,
    finish_description,
    reserve_description,
)
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.integration import _seed
from tests.test_chat_image_description_receipts import seed_receipt_request

pytestmark = pytest.mark.integration


@pytest.fixture
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


async def test_contending_image_claims_dispatch_once_and_obey_rls_and_cascade(
    lab: RlsLab, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_engine = create_async_engine(async_url(lab.admin_url))
    app_engine = create_async_engine(async_url(lab.app_url))
    admin = async_sessionmaker(admin_engine, expire_on_commit=False)
    app = async_sessionmaker(app_engine, class_=ShieldedAsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(_seed, "TestSession", admin)
    try:
        conversation_id = await _seed.seed_conversation_with_agent()
        async with admin() as db:
            request = await seed_receipt_request(db, conversation_id)
        context = request["context"]
        ready = asyncio.Barrier(2)

        async def claim() -> DescriptionReservation:
            async with tenant_session(context, maker=app) as db:
                await db.connection()
                await ready.wait()
                return await reserve_description(db, **request)

        claims = await asyncio.wait_for(asyncio.gather(claim(), claim()), timeout=15)
        assert sorted(result.status for result in claims) == ["acquired", "pending"]
        acquired = next(result for result in claims if result.status == "acquired")
        assert acquired.receipt_id is not None
        assert {result.receipt_id for result in claims} == {acquired.receipt_id}

        # Reopening both a losing claim and a successful one must use the first receipt.
        async with tenant_session(context, maker=app) as db:
            assert (await reserve_description(db, **request)).status == "pending"
            assert await finish_description(
                db,
                context=context,
                run_id=request["run_id"],
                receipt_id=acquired.receipt_id,
                description="파란 원",
            )
            replay = await reserve_description(db, **request)
            assert (replay.status, replay.description) == ("completed", "파란 원")

        # A raw table read bypasses application filters, so this exercises the RLS policy.
        receipt_ids = select(ChatImageDescription.id)
        async with tenant_session(replace(context, tenant_id=lab.tenant_b), maker=app) as db:
            assert list(await db.scalars(receipt_ids)) == []
        async with tenant_session(context, maker=app) as db:
            assert list(await db.scalars(receipt_ids)) == [acquired.receipt_id]
            await db.execute(
                delete(MessageAttachment).where(MessageAttachment.id == request["attachment_id"])
            )
            await db.commit()
        async with admin() as db:
            assert list(await db.scalars(receipt_ids)) == []
    finally:
        await app_engine.dispose()
        await admin_engine.dispose()
