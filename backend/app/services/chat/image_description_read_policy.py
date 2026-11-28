"""Request-local authorization for a private conversation's recorded image descriptions."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from itertools import batched
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_run_scope import private_run_predicate
from app.authz.org_context import OrgContext
from app.authz.revocations import DecisionStamp, stamp_is_current
from app.authz.run_decisions import fresh_run_decision_scope
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.services.chat.image_description_provider import (
    ImageDescriptionDenied,
    ImageDescriptionReadGrant,
    ImageDescriptionReason,
    load_image_description_policy,
    load_image_description_route,
    validate_image_description_attachment,
)


@dataclass(frozen=True, slots=True)
class ImageDescriptionReadPolicy:
    context: OrgContext
    conversation_id: UUID
    stamp: DecisionStamp
    grants: dict[UUID, ImageDescriptionReadGrant | ImageDescriptionReason]

    def grant_for(self, run_id: UUID, attachment: MessageAttachment) -> ImageDescriptionReadGrant:
        """Bind the actual private upload from the file list to an authorized run in its parent."""
        if (
            attachment.conversation_id != self.conversation_id
            or attachment.tenant_id != self.context.tenant_id
            or attachment.org_id != self.context.org_id
            or attachment.user_id != self.context.user_id
        ):
            raise ImageDescriptionDenied("access_denied")
        grant = self.grants.get(run_id, "access_denied")
        if isinstance(grant, str):
            raise ImageDescriptionDenied(grant)
        validate_image_description_attachment(attachment, grant.max_input_bytes)
        return grant

    async def still_current(self, db: AsyncSession) -> bool:
        """Recheck grants after file IO, including time-limited grants and catalog changes."""
        try:
            latest = await resolve_image_description_read_policy(
                db,
                context=self.context,
                conversation_id=self.conversation_id,
                run_ids=self.grants.keys(),
            )
        except ImageDescriptionDenied:
            return False
        return latest == self and await stamp_is_current(db, self.stamp)


async def resolve_image_description_read_policy(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    run_ids: Collection[UUID],
) -> ImageDescriptionReadPolicy:
    """Authorize each distinct server-attributed credential subject once per projection pass."""
    try:
        with fresh_run_decision_scope():
            org, tenant, policy = await load_image_description_policy(db, context)
            route = await load_image_description_route(db, org, tenant, policy)
            stamp = DecisionStamp(org.id, route.epoch)
            subjects: dict[UUID, UUID] = {}
            for batch in batched(dict.fromkeys(run_ids), 500):
                pairs = await db.execute(
                    select(ConversationRun.id, ConversationRun.credential_subject_user_id).where(
                        ConversationRun.id.in_(batch),
                        ConversationRun.conversation_id == conversation_id,
                        ConversationRun.tenant_id == context.tenant_id,
                        ConversationRun.org_id == context.org_id,
                        ConversationRun.run_source == "CHAT",
                        private_run_predicate(context),
                    )
                )
                subjects.update(
                    (run_id, subject) for run_id, subject in pairs if subject is not None
                )
            decisions: dict[UUID, ImageDescriptionReadGrant | ImageDescriptionReason] = {}
            for subject in set(subjects.values()):
                try:
                    decisions[subject] = await route.authorize(db, context, subject)
                except ImageDescriptionDenied as exc:
                    decisions[subject] = exc.code
                except AppError:
                    decisions[subject] = "access_denied"
            return ImageDescriptionReadPolicy(
                context,
                conversation_id,
                stamp,
                {run_id: decisions[subject] for run_id, subject in subjects.items()},
            )
    except AppError:
        raise ImageDescriptionDenied("access_denied") from None
    except ValueError:
        raise ImageDescriptionDenied("model_unavailable") from None
