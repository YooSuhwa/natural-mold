"""Settle a measured auxiliary call without re-opening access to its image or caption."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.conversation_run import ConversationRun
from app.models.image_description_charge import ImageDescriptionCharge
from app.models.model import Model
from app.models.token_usage import TokenUsage
from app.services.spend_flush import write_daily_spend
from app.services.spend_writer import SpendEntry
from app.tenancy.attribution import usage_columns


@dataclass(frozen=True, slots=True)
class ImageDescriptionBilling:
    model_name: str
    credential_scope: Literal["tenant", "personal"]
    input_rate: Decimal
    output_rate: Decimal


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def claim_image_description_usage(
    db: AsyncSession,
    *,
    receipt_id: UUID,
    run_id: UUID,
    model_id: UUID,
    billing: ImageDescriptionBilling,
) -> str:
    """Stage the claim in the receipt's transaction, before contacting the provider."""
    token = secrets.token_hex(32)
    db.add(
        ImageDescriptionCharge(
            id=receipt_id,
            run_id=run_id,
            model_id=model_id,
            model_name=billing.model_name,
            credential_scope=billing.credential_scope,
            input_rate=billing.input_rate,
            output_rate=billing.output_rate,
            settlement_digest=_digest(token),
        )
    )
    return token


async def settle_image_description_usage(
    db: AsyncSession,
    *,
    context: OrgContext,
    run_id: UUID,
    receipt_id: UUID,
    settlement_token: str,
    input_tokens: int,
    output_tokens: int,
) -> bool:
    """Consume the winner's one-time capability and book provider usage in one transaction.

    This operation grants no resource access. The same tenant session, frozen run
    identity and a genuine claim are required even after a membership or upload is gone.
    """
    if input_tokens < 0 or output_tokens < 0 or not settlement_token:
        return False
    run = await db.scalar(
        select(ConversationRun).where(
            ConversationRun.id == run_id, private_scope_predicate(ConversationRun, context)
        )
    )
    if run is None:
        return False
    charge = await db.scalar(
        update(ImageDescriptionCharge)
        .where(
            ImageDescriptionCharge.id == receipt_id,
            ImageDescriptionCharge.run_id == run.id,
            ImageDescriptionCharge.settlement_digest == _digest(settlement_token),
        )
        .values(settlement_digest=None)
        .returning(ImageDescriptionCharge)
    )
    if charge is None:
        return False
    cost = charge.input_rate * input_tokens + charge.output_rate * output_tokens
    # run_id uniquely identifies the primary model's posting. This supplemental
    # posting retains its own run FK and the immutable billing claim identity.
    db.add(
        TokenUsage(
            id=charge.id,
            auxiliary_run_id=run.id,
            conversation_id=run.conversation_id,
            agent_id=run.agent_id,
            model_name=charge.model_name[:100],
            llm_credential_scope=charge.credential_scope,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated_cost=cost,
            **usage_columns(run),
        )
    )
    # A catalog model can be retired while the authorized call is in flight.
    # The claim retains its identity and rates; only the aggregate with a live model FK needs it.
    live_model = await db.scalar(
        select(Model.id)
        .where(Model.id == charge.model_id)
        .with_for_update(read=True, key_share=True)
    )
    await write_daily_spend(
        db,
        [
            SpendEntry(
                date=datetime.now(UTC).date(),
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                org_id=context.org_id,
                agent_id=run.agent_id,
                model_id=live_model,
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                cost_usd=cost,
            )
        ],
    )
    await db.commit()
    return True
