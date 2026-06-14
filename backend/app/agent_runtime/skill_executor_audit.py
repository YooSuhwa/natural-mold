from __future__ import annotations

import logging

from app.database import async_session
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)

logger = logging.getLogger(__name__)


async def record_credential_audits(
    ctx: SkillToolContext,
    descriptor: SkillRuntimeDescriptor,
    *,
    injected_credentials: list[tuple[str, ResolvedCredential]],
    executable: str,
    timeout_seconds: float,
) -> None:
    if ctx.user_id is None or not injected_credentials:
        return
    try:
        await _write_credential_audits(
            ctx,
            descriptor,
            injected_credentials=injected_credentials,
            executable=executable,
            timeout_seconds=timeout_seconds,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "skill credential audit write failed skill=%s thread=%s",
            descriptor.slug,
            ctx.thread_id,
            exc_info=True,
        )


async def record_sandbox_denial(
    ctx: SkillToolContext,
    descriptor: SkillRuntimeDescriptor,
    *,
    reason_code: str,
    executable: str,
) -> None:
    try:
        await _write_sandbox_denial(
            ctx,
            descriptor,
            reason_code=reason_code,
            executable=executable,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "skill sandbox audit write failed skill=%s thread=%s",
            descriptor.slug,
            ctx.thread_id,
            exc_info=True,
        )


async def _write_credential_audits(
    ctx: SkillToolContext,
    descriptor: SkillRuntimeDescriptor,
    *,
    injected_credentials: list[tuple[str, ResolvedCredential]],
    executable: str,
    timeout_seconds: float,
) -> None:
    from app.credentials import service as credential_service

    seen: set[str] = set()
    async with async_session() as db:
        for requirement_key, resolved in injected_credentials:
            credential_key = str(resolved.credential_id)
            if credential_key in seen:
                continue
            seen.add(credential_key)
            metadata: dict[str, object] = {
                "kind": "execute_in_skill",
                "skill_id": str(descriptor.id),
                "skill_slug": descriptor.slug,
                "requirement_key": requirement_key,
                "thread_id": ctx.thread_id,
                "command_executable": executable,
                "timeout_seconds": timeout_seconds,
            }
            if ctx.agent_id is not None:
                metadata["agent_id"] = str(ctx.agent_id)
            if ctx.run_id is not None:
                metadata["run_id"] = ctx.run_id
            await credential_service.write_audit_log(
                db,
                credential_id=resolved.credential_id,
                actor_user_id=ctx.user_id,
                action="invoke",
                source="runtime",
                metadata=metadata,
            )
        await db.commit()


async def _write_sandbox_denial(
    ctx: SkillToolContext,
    descriptor: SkillRuntimeDescriptor,
    *,
    reason_code: str,
    executable: str,
) -> None:
    from app.services import audit_service

    metadata: dict[str, object] = {
        "kind": "execute_in_skill",
        "skill_id": str(descriptor.id),
        "skill_slug": descriptor.slug,
        "thread_id": ctx.thread_id,
        "command_executable": executable,
    }
    async with async_session() as db:
        await audit_service.record_event(
            db,
            actor_type="user" if ctx.user_id is not None else "system",
            actor_user_id=ctx.user_id,
            owner_user_id=ctx.user_id,
            action="skill_security.sandbox_denied",
            target_type="skill",
            target_id=descriptor.id,
            target_owner_user_id=ctx.user_id,
            outcome="denied",
            reason_code=reason_code,
            run_id=ctx.run_id,
            metadata=metadata,
        )
        await db.commit()
