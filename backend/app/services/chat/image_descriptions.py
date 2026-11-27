"""Auxiliary image descriptions are scoped to an upload version, question, model and policy."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.database import tenant_sessions
from app.models.message_attachment import MessageAttachment
from app.schemas.conversation import ImageDescriptionReady, ImageDescriptionUnavailable
from app.services.chat.image_description_admission import (
    DeniedImageDescription,
    accepted_image_description,
)
from app.services.chat.image_description_input import (
    MAX_QUESTION_CHARS,
    InvalidDescriptionInput,
    description_image,
    matches_version,
    question_digest,
    read_version,
    scoped_image_path,
)
from app.services.chat.image_description_provider import (
    ImageDescriptionDenied,
    ImageDescriptionTarget,
    describe_image,
    resolve_image_description_target,
)
from app.services.chat.image_description_receipts import (
    DescriptionReservation,
    find_description,
    finish_description,
    reserve_description,
)
from app.services.chat.image_description_usage import (
    ImageDescriptionBilling,
    settle_image_description_usage,
)
from app.storage.paths import ForeignTenantPathError

logger = logging.getLogger(__name__)
Description = ImageDescriptionReady | ImageDescriptionUnavailable
_REASONS = {
    "policy_disabled": "policy_denied",
    "access_denied": "policy_denied",
    "model_unavailable": "unavailable",
    "budget_limit": "budget",
    "provider_timeout": "timeout",
    "provider_failed": "provider_error",
}
_MODEL_NOTES = {
    "policy_denied": "이미지 설명을 사용할 권한이 없습니다",
    "size_limit": "이미지나 질문이 설정된 크기 한도를 넘었습니다",
    "invalid_image": "이미지 파일을 읽을 수 없습니다",
    "budget": "허용된 이미지 설명 비용을 초과합니다",
    "timeout": "이미지 설명 요청 시간이 만료되었습니다",
    "pending": "이미지 설명을 확인하고 있습니다",
    "unconfirmed": "실행이 끝나 이미지 설명 요청의 처리 결과를 확인할 수 없습니다",
    "provider_error": "보조 모델이 이미지 설명을 반환하지 못했습니다",
    "unavailable": "이 첨부 버전의 이미지 설명을 사용할 수 없습니다",
}


class _AttachmentScope(TypedDict):
    context: OrgContext
    conversation_id: uuid.UUID
    attachment_id: uuid.UUID


class _Version(TypedDict):
    content_sha256: str
    model_id: uuid.UUID
    policy_key: str


async def _checked_target(
    db: AsyncSession,
    scope: _AttachmentScope,
    run_id: uuid.UUID,
    key: _Version,
    *,
    require_running: bool = False,
) -> ImageDescriptionTarget:
    target = await resolve_image_description_target(
        db, **scope, run_id=run_id, require_running=require_running
    )
    if (
        target.model_id != key["model_id"]
        or target.policy_fingerprint != key["policy_key"]
        or target.attachment.content_sha256 != key["content_sha256"]
    ):
        raise InvalidDescriptionInput("unavailable")
    return target


def description_unavailable(code: str | None) -> ImageDescriptionUnavailable:
    reason = _REASONS.get(code or "", code or "")
    return ImageDescriptionUnavailable(reason=reason if reason in _MODEL_NOTES else "unavailable")


async def pin_description(
    db: AsyncSession,
    *,
    ref: dict[str, str],
    attachment: MessageAttachment,
    context: OrgContext,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    question: str,
    root_model_id: str | None,
) -> None:
    """Keep the server's immutable references in the human message, without storing image data."""
    if not question:
        return
    accepted = await accepted_image_description(
        db,
        context=context,
        conversation_id=conversation_id,
        run_id=run_id,
        root_model_id=root_model_id,
        attachment_id=attachment.id,
    )
    if accepted is None:
        return
    if isinstance(accepted, DeniedImageDescription):
        ref.update(form="image_description", description_error=accepted.reason)
        return
    try:
        target = await resolve_image_description_target(
            db,
            context=context,
            run_id=run_id,
            conversation_id=conversation_id,
            attachment_id=attachment.id,
        )
    except ImageDescriptionDenied as exc:
        if exc.code != "policy_disabled":
            ref.update(
                form="image_description", description_error=description_unavailable(exc.code).reason
            )
        return
    ref["form"] = "image_description"
    if len(question) > MAX_QUESTION_CHARS or attachment.size_bytes > target.max_input_bytes:
        ref["description_error"] = "size_limit"
        return
    if (
        target.model_id != accepted.model_id
        or target.policy_fingerprint != accepted.policy_fingerprint
        or (attachment.content_sha256 and attachment.content_sha256 != accepted.content_sha256)
    ):
        ref["description_error"] = "unavailable"
        return
    ref.update(
        description_run=str(run_id),
        content_sha256=accepted.content_sha256,
        question_sha256=question_digest(question),
        description_model=str(target.model_id),
        description_policy=target.policy_fingerprint,
    )


@dataclass(frozen=True, slots=True)
class DescriptionRun:
    context: OrgContext
    conversation_id: uuid.UUID
    run_id: uuid.UUID
    sessions: Callable[[], AsyncSession]

    @classmethod
    def for_run(
        cls,
        context: OrgContext,
        conversation_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> DescriptionRun:
        return cls(context, conversation_id, run_id, tenant_sessions(context))

    async def model_note(self, ref: Mapping[str, Any], question: str) -> str:
        if (
            not question
            or len(question) > MAX_QUESTION_CHARS
            or ("question_sha256" in ref and question_digest(question) != ref["question_sha256"])
        ):
            return _MODEL_NOTES["unavailable"]
        try:
            result = await self.resolve(ref, question=question)
        except Exception:
            # An unavailable auxiliary model or DB must not abort the text conversation.
            logger.warning("image description unavailable for run %s", self.run_id)
            return _MODEL_NOTES["unavailable"]
        if isinstance(result, ImageDescriptionReady):
            return f"첨부 이미지 설명 (/api/uploads/{ref['id']}): {result.text}"
        return _MODEL_NOTES[result.reason]

    async def resolve(
        self,
        ref: Mapping[str, Any],
        *,
        question: str,
    ) -> Description:
        if error := ref.get("description_error"):
            return description_unavailable(str(error))
        try:
            attachment_id = uuid.UUID(str(ref["id"]))
            model_id = uuid.UUID(str(ref["description_model"]))
            content_digest = str(ref["content_sha256"])
            question_sha = str(ref["question_sha256"])
            policy_key = str(ref["description_policy"])
        except (ValueError, KeyError):
            return description_unavailable("unavailable")
        scope: _AttachmentScope = {
            "context": self.context,
            "conversation_id": self.conversation_id,
            "attachment_id": attachment_id,
        }
        key: _Version = {
            "content_sha256": content_digest,
            "model_id": model_id,
            "policy_key": policy_key,
        }
        can_start = ref.get("description_run") == str(self.run_id)
        try:
            async with self.sessions() as db:
                target = await _checked_target(db, scope, self.run_id, key)
                path = scoped_image_path(target.attachment.storage_path, self.context.tenant_id)
                found = await find_description(db, **scope, **key, question_sha256=question_sha)
                if not can_start or found.status != "unavailable":
                    if found.status == "completed" and not await asyncio.to_thread(
                        matches_version, path, target.max_input_bytes, content_digest
                    ):
                        return description_unavailable("unavailable")
                    return receipt_result(found)
                source, digest = await asyncio.to_thread(read_version, path, target.max_input_bytes)
                if digest != content_digest:
                    return description_unavailable("unavailable")
                claim = await reserve_description(
                    db,
                    **scope,
                    **key,
                    question=question,
                    run_id=self.run_id,
                    billing=ImageDescriptionBilling(
                        target.model_name,
                        target.credential_scope,
                        target.input_rate,
                        target.output_rate,
                    ),
                )
                if (
                    claim.status != "acquired"
                    or claim.receipt_id is None
                    or claim.settlement_token is None
                ):
                    return receipt_result(claim)
            # Check the grant again immediately before dispatch.
            receipt_id = claim.receipt_id
            try:
                image, mime = await asyncio.to_thread(
                    description_image, source, target.attachment.mime_type, target.max_input_bytes
                )
                async with self.sessions() as db:
                    allowed = await _checked_target(
                        db, scope, self.run_id, key, require_running=True
                    )
                answer = await describe_image(
                    allowed, image=image, mime_type=mime, question=question
                )
            except (ImageDescriptionDenied, InvalidDescriptionInput) as exc:
                failure = description_unavailable(exc.code)
                async with self.sessions() as db:
                    await finish_description(
                        db,
                        context=self.context,
                        receipt_id=receipt_id,
                        run_id=self.run_id,
                        failure_code=failure.reason,
                    )
                return failure
            async with self.sessions() as db:
                posted = await settle_image_description_usage(
                    db,
                    context=self.context,
                    run_id=self.run_id,
                    receipt_id=receipt_id,
                    settlement_token=claim.settlement_token,
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                )
                if not posted:
                    return description_unavailable("unavailable")
                # Accounting remains valid even if an in-flight attachment or policy was revoked.
                current = await _checked_target(db, scope, self.run_id, key)
                current_path = scoped_image_path(
                    current.attachment.storage_path, self.context.tenant_id
                )
                if not await asyncio.to_thread(
                    matches_version, current_path, current.max_input_bytes, content_digest
                ):
                    return description_unavailable("unavailable")
                finished = await finish_description(
                    db,
                    context=self.context,
                    receipt_id=receipt_id,
                    run_id=self.run_id,
                    description=answer.description,
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                    estimated_cost_usd=answer.estimated_cost_usd,
                    credential_scope=answer.credential_scope,
                )
                if not finished:
                    return description_unavailable("unavailable")
                return ImageDescriptionReady(text=answer.description)
        except (ImageDescriptionDenied, InvalidDescriptionInput) as exc:
            return description_unavailable(exc.code)
        except ForeignTenantPathError:
            return description_unavailable("unavailable")


def receipt_result(found: DescriptionReservation) -> Description:
    if found.status == "completed" and found.description:
        return ImageDescriptionReady(text=found.description)
    return description_unavailable(found.failure_code or found.status)
