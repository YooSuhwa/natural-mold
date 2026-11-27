"""Resolve one organization-approved destination for the run's own attachment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.attachment_scope import private_attachment_predicate
from app.authz.company_catalog import company_tier, stored_policy
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.private_actor import require_private_actor
from app.authz.run_decisions import fresh_run_decision_scope, require_decided
from app.authz.runtime import runtime_authz
from app.authz.service_types import Verdict
from app.credentials.boundary import open_values
from app.credentials.company_use import company_credential_usable
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.models.credential import Credential
from app.models.message_attachment import MessageAttachment
from app.models.model import Model
from app.models.organization import Organization, Tenant
from app.outbound.hosts import host_allowed, host_of
from app.schemas.org_image_description import ImageDescriptionPolicy, https_base_url
from app.services.chat.image_description_wire import (
    ImageDescriptionWireError,
    send_image_description,
)

CredentialScope = Literal["personal", "tenant"]
ImageDescriptionReason = Literal[
    "policy_disabled",
    "access_denied",
    "model_unavailable",
    "size_limit",
    "budget_limit",
    "provider_timeout",
    "provider_failed",
]
_CANONICAL_BASES = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
_IMAGE_TYPES = frozenset({"image/png", "image/webp", "image/jpeg", "image/gif"})


class ImageDescriptionDenied(Exception):
    def __init__(self, code: ImageDescriptionReason) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ImageDescriptionTarget:
    attachment: MessageAttachment = field(repr=False)
    model_id: UUID
    policy_fingerprint: str
    max_input_bytes: int
    context_window: int
    model_name: str
    policy: ImageDescriptionPolicy
    credential_scope: CredentialScope
    input_rate: Decimal
    output_rate: Decimal
    api_key: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class ImageDescriptionResult:
    description: str = field(repr=False)
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    credential_scope: CredentialScope


async def _require_use(context: OrgContext, resource: str) -> None:
    await require_decided(
        runtime_authz(),
        Principal.for_user(context),
        "can_use",
        resource,
        min_visible="can_use",
        resource_org_id=context.org_id,
        legacy=Verdict.ALLOW,
        not_found=authorization_error("not_found"),
    )


async def _check_credential(
    db: AsyncSession, credential: Credential, context: OrgContext
) -> CredentialScope:
    if credential.status != "active":
        raise ImageDescriptionDenied("access_denied")
    if credential.scope == "tenant":
        if not await company_credential_usable(
            db,
            credential,
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            org_id=context.org_id,
        ):
            raise ImageDescriptionDenied("access_denied")
        return "tenant"
    if (
        credential.scope != "personal"
        or credential.is_system
        or credential.user_id != context.user_id
        or credential.org_id != context.org_id
        or credential.tenant_id != context.tenant_id
    ):
        raise ImageDescriptionDenied("access_denied")
    await _require_use(context, f"credential:{credential.id}")
    return "personal"


def _validate_model(
    model: Model, policy: ImageDescriptionPolicy, org: Organization, tenant: Tenant
) -> tuple[Decimal, Decimal]:
    if (
        model.tenant_id not in (None, tenant.id)
        or model.kind != "chat"
        or model.supports_vision is not True
        or model.provider != policy.provider
    ):
        raise ImageDescriptionDenied("model_unavailable")
    company = company_tier(tenant)
    source = "company" if model.tenant_id else "platform"
    if not (
        company.offers("model", model.id, source, available=model.is_visible)
        and company.reaches("model", model.id, source, org.id)
        and stored_policy(org.settings).enabled_for("model", model.id)
    ):
        raise ImageDescriptionDenied("access_denied")
    base = model.base_url or _CANONICAL_BASES.get(policy.provider)
    if not base or https_base_url(base) != policy.base_url:
        raise ImageDescriptionDenied("model_unavailable")
    # An official key may only be sent to its official origin. A compatible endpoint
    # is checked against the credential issuer's own base URL at dispatch.
    if policy.provider == "openai" and policy.base_url != _CANONICAL_BASES["openai"]:
        raise ImageDescriptionDenied("model_unavailable")
    rate_in, rate_out = model.cost_per_input_token, model.cost_per_output_token
    if (
        rate_in is None
        or rate_out is None
        or rate_in < 0
        or rate_out < 0
        or not model.context_window
        or model.context_window < 1
    ):
        raise ImageDescriptionDenied("budget_limit")
    # Bound the entire input context, including provider-specific image tokens; a
    # byte-to-token heuristic can't prove a hard pre-send cost ceiling.
    cap = rate_in * model.context_window + rate_out * policy.max_output_tokens
    if cap > policy.max_cost_usd:
        raise ImageDescriptionDenied("budget_limit")
    return rate_in, rate_out


async def _dispatch_key(credential: Credential, policy: ImageDescriptionPolicy) -> str:
    try:
        values = await open_values(credential, "run")
    except Exception:
        raise ImageDescriptionDenied("model_unavailable") from None
    key = values.get("api_key")
    base = values.get("base_url") or _CANONICAL_BASES.get(policy.provider)
    if not isinstance(key, str) or not key or not isinstance(base, str):
        raise ImageDescriptionDenied("model_unavailable")
    if https_base_url(base) != policy.base_url:
        raise ImageDescriptionDenied("access_denied")
    return key


async def resolve_image_description_target(
    db: AsyncSession,
    *,
    context: OrgContext,
    run_id: UUID,
    conversation_id: UUID,
    attachment_id: UUID,
    require_running: bool = False,
) -> ImageDescriptionTarget:
    """Recheck current authority. Read/accept can pin a target but never open a key."""
    try:
        with fresh_run_decision_scope():
            return await _resolve_target(
                db, context, run_id, conversation_id, attachment_id, require_running
            )
    except AppError:
        raise ImageDescriptionDenied("access_denied") from None
    except (ValidationError, ValueError):
        raise ImageDescriptionDenied("model_unavailable") from None


async def _resolve_target(
    db: AsyncSession,
    context: OrgContext,
    run_id: UUID,
    conversation_id: UUID,
    attachment_id: UUID,
    require_running: bool,
) -> ImageDescriptionTarget:
    await require_private_actor(db, context)
    org = await db.get(Organization, context.org_id, populate_existing=True)
    tenant = await db.get(Tenant, context.tenant_id, populate_existing=True)
    if not org or not tenant or org.tenant_id != tenant.id:
        raise ImageDescriptionDenied("access_denied")
    configured = (org.settings or {}).get("image_description")
    if configured is None:
        raise ImageDescriptionDenied("policy_disabled")
    policy = ImageDescriptionPolicy.model_validate(configured)
    run = await db.scalar(
        select(ConversationRun)
        .where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.tenant_id == tenant.id,
            ConversationRun.org_id == org.id,
            private_run_predicate(context),
        )
        .execution_options(populate_existing=True)
    )
    if not run or run.run_source != "CHAT" or run.credential_subject_user_id is None:
        raise ImageDescriptionDenied("access_denied")
    if require_running and (
        run.status != "running" or not run.is_active or run.cancel_requested_at
    ):
        raise ImageDescriptionDenied("access_denied")
    attachment = await db.scalar(
        select(MessageAttachment)
        .where(
            MessageAttachment.id == attachment_id,
            MessageAttachment.conversation_id == conversation_id,
            private_attachment_predicate(context),
        )
        .execution_options(populate_existing=True)
    )
    if not attachment or attachment.mime_type.lower() not in _IMAGE_TYPES:
        raise ImageDescriptionDenied("access_denied")
    if not 0 < attachment.size_bytes <= policy.max_input_bytes:
        raise ImageDescriptionDenied("size_limit")
    subject = replace(context, user_id=run.credential_subject_user_id)
    if subject.user_id != context.user_id:
        await require_private_actor(db, subject)
    model = await db.get(Model, policy.model_id, populate_existing=True)
    if not model or model.default_credential_id is None:
        raise ImageDescriptionDenied("model_unavailable")
    rate_in, rate_out = _validate_model(model, policy, org, tenant)
    await _require_use(subject, f"llm_model:{model.id}")
    credential = await db.get(Credential, model.default_credential_id, populate_existing=True)
    if not credential or credential.definition_key != policy.provider:
        raise ImageDescriptionDenied("model_unavailable")
    scope = await _check_credential(db, credential, subject)
    if credential.allowed_hosts and not host_allowed(
        host_of(policy.base_url), credential.allowed_hosts
    ):
        raise ImageDescriptionDenied("access_denied")
    binding = {
        "policy": policy.model_dump(mode="json"),
        "authority_version": org.authz_epoch,
        "name": model.model_name,
        "input_rate": str(rate_in),
        "output_rate": str(rate_out),
        "context_window": model.context_window,
        "credential_id": str(credential.id),
        "credential_version": credential.updated_at.isoformat(),
        "subject": str(subject.user_id),
    }
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    key = await _dispatch_key(credential, policy) if require_running else None
    return ImageDescriptionTarget(
        attachment,
        model.id,
        fingerprint,
        policy.max_input_bytes,
        int(model.context_window or 0),
        model.model_name,
        policy,
        scope,
        rate_in,
        rate_out,
        key,
    )


async def describe_image(
    target: ImageDescriptionTarget, *, image: bytes, mime_type: str, question: str
) -> ImageDescriptionResult:
    """Send a validated raster and this turn's question once; no chat history or file names."""
    if target.api_key is None or mime_type not in _IMAGE_TYPES:
        raise ImageDescriptionDenied("access_denied")
    if not (0 < len(image) <= target.max_input_bytes) or (
        not question.strip() or len(question) > 2000
    ):
        raise ImageDescriptionDenied("size_limit")
    try:
        description, incoming, outgoing = await send_image_description(
            endpoint=target.policy.base_url + "/chat/completions",
            api_key=target.api_key,
            model_name=target.model_name,
            image=image,
            mime_type=mime_type,
            question=question,
            max_output_tokens=target.policy.max_output_tokens,
            timeout_seconds=target.policy.timeout_seconds,
        )
    except ImageDescriptionWireError as exc:
        raise ImageDescriptionDenied(exc.code) from None
    if incoming > target.context_window:
        raise ImageDescriptionDenied("provider_failed")
    return ImageDescriptionResult(
        description,
        incoming,
        outgoing,
        incoming * target.input_rate + outgoing * target.output_rate,
        target.credential_scope,
    )
