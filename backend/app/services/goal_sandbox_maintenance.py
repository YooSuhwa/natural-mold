"""Fence idle provider operations and recover an interrupted outbound-file publication."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import goal_sandbox_cleanup_provider as provider
from app.agent_runtime.goal_sandbox_provider import (
    output_manifest,
    publish_outputs,
    valid_output_name,
)
from app.agent_runtime.skill_sandbox import (
    MAX_OUTPUT_FILE_BYTES,
    MAX_OUTPUT_TOTAL_BYTES,
    SkillSandboxError,
)
from app.config import settings
from app.database import SessionTenant, platform_session, tenant_sessions
from app.models.conversation import Conversation
from app.models.goal_sandbox import GoalSandbox
from app.models.organization import Organization, Tenant
from app.services import goal_sandbox_leases as leases
from app.services.cancel_deferred import finish_then_cancel
from app.services.goal_sandbox_artifacts import check_goal_output_destinations, index_goal_outputs
from app.services.goal_sandbox_policy import goal_scope_is_current
from app.services.goal_sandbox_profile import goal_sandbox_provider_fingerprint

logger = logging.getLogger(__name__)
_OPERATION_TTL = timedelta(minutes=3)
# Leave ample time to commit/release the database fence after the provider returns.
_PROVIDER_TIMEOUT = 75
_PER_ORG_BATCH = 10


class OutputConflict(ValueError):
    """An existing canonical path does not contain the exact recovered output."""


def _missing_outputs(directory: Path, manifest: dict[str, str]) -> set[str]:
    missing = set(manifest)
    if any(not valid_output_name(name) for name in missing) or len(manifest) > 1024:
        raise OutputConflict
    if not missing:
        return missing
    try:
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return missing
    except (NotADirectoryError, IsADirectoryError) as exc:
        raise OutputConflict from exc
    total = 0
    try:
        for name, digest in manifest.items():
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise OutputConflict from exc
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OUTPUT_FILE_BYTES:
                    raise OutputConflict
                total += info.st_size
                if total > MAX_OUTPUT_TOTAL_BYTES:
                    raise OutputConflict
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    actual = hashlib.sha256()
                    remaining = info.st_size
                    while remaining:
                        chunk = stream.read(min(remaining, 65536))
                        if not chunk:
                            raise OutputConflict
                        remaining -= len(chunk)
                        actual.update(chunk)
                    if stream.read(1) or actual.hexdigest() != digest:
                        raise OutputConflict
                missing.remove(name)
            finally:
                os.close(fd)
    finally:
        os.close(dfd)
    return missing


async def _recover(db: AsyncSession, claim: leases.SandboxClaim) -> None:
    manifest = claim.pending_manifest
    if manifest is None or (manifest and claim.output_run_id is None):
        await leases.mark_unresolved(db, claim, "unconfirmed_writer")
        return
    directory = Path(settings.conversation_output_dir) / str(claim.scope.conversation_id)
    try:
        if claim.output_run_id is not None:
            await check_goal_output_destinations(
                db, scope=claim.scope, run_id=claim.output_run_id, manifest=manifest
            )
        missing = await asyncio.to_thread(_missing_outputs, directory, manifest)
        if missing:
            files = await provider.read_completed_outputs(
                claim.workspace_id, claim.provider_id, missing
            )
            if files is None:
                await leases.mark_unresolved(db, claim, "provider_missing")
                return
            expected = {name: manifest[name] for name in missing}
            if output_manifest(files) != expected:
                await leases.mark_unresolved(db, claim, "corrupt_output")
                return
            # publish_outputs atomically links each file, verifying any replay;
            # the workspace and current policy stay locked through publication.
            await finish_then_cancel(asyncio.to_thread(publish_outputs, directory, files))
        if manifest and claim.output_run_id is not None:
            await index_goal_outputs(
                db,
                scope=claim.scope,
                run_id=claim.output_run_id,
                directory=directory,
                manifest=manifest,
            )
    except (OutputConflict, SkillSandboxError):
        await leases.mark_unresolved(db, claim, "output_conflict")
        return
    await leases.complete_maintenance(db, claim, manifest=manifest)


async def _apply(db: AsyncSession, claim: leases.SandboxClaim) -> None:
    scope = claim.scope
    # Keep the permission locks in the same order as a normal writer. A stale
    # receipt must not recreate a deleted conversation or overwrite a new goal.
    scope_current = claim.action != "recover" or await goal_scope_is_current(db, scope)
    if not await leases.lock_operation(db, claim):
        return
    if not scope_current:
        exists = await db.scalar(
            select(Conversation.id)
            .where(
                Conversation.id == scope.conversation_id,
                Conversation.tenant_id == scope.tenant_id,
                Conversation.org_id == scope.org_id,
                Conversation.user_id == scope.user_id,
            )
            .with_for_update()
        )
        if exists is None:
            await leases.retire_erased_scope(db, claim)
        else:
            await leases.mark_unresolved(db, claim, "scope_changed")
        return
    if scope.image != goal_sandbox_provider_fingerprint():
        # A 404 from the wrong endpoint is not a deletion receipt.
        await leases.fail_maintenance(db, claim)
        return
    async with asyncio.timeout(_PROVIDER_TIMEOUT):
        if claim.action == "recover":
            await _recover(db, claim)
            return
        if claim.action == "pause":
            confirmed = await provider.pause(claim.workspace_id, claim.provider_id)
        elif claim.action == "delete":
            confirmed = await provider.delete(claim.workspace_id, claim.provider_id)
        else:
            raise ValueError("Unexpected workspace maintenance operation.")
    if confirmed:
        await leases.complete_maintenance(db, claim)
    else:
        await leases.fail_maintenance(db, claim)


async def _sweep_org(
    tenant_id: UUID, org_id: UUID, maker: Callable[[], AsyncSession] | None
) -> None:
    db_scope = SessionTenant(tenant_id)
    scoped = tenant_sessions(db_scope) if maker is None else tenant_sessions(db_scope, maker=maker)
    for _ in range(_PER_ORG_BATCH):
        async with scoped() as db:
            owning_org = (
                await db.execute(
                    select(Organization, Tenant.status)
                    .join(Tenant, Tenant.id == Organization.tenant_id)
                    .where(Organization.id == org_id, Organization.tenant_id == tenant_id)
                )
            ).first()
            if owning_org is None:
                return
            org, tenant_status = owning_org
            opted_in = (
                settings.skill_sandbox_enabled
                and settings.goal_sandbox_enabled
                and tenant_status == "active"
                and org.status == "active"
                and (org.settings or {}).get("goal_sandbox_enabled") is True
            )
            now = datetime.now(UTC)
            claim = await leases.claim_maintenance(
                db,
                tenant_id,
                org_id=org_id,
                ttl=_OPERATION_TTL,
                idle_before=now
                - timedelta(
                    seconds=min(
                        settings.goal_sandbox_idle_seconds, settings.goal_sandbox_retention_seconds
                    )
                ),
                expire_before=now - timedelta(seconds=settings.goal_sandbox_retention_seconds),
                disabled=not opted_in,
            )
            await db.commit()
        if claim is None:
            return
        try:
            async with scoped() as db:
                await _apply(db, claim)
                await db.commit()
        except Exception as exc:
            logger.warning(
                "Workspace maintenance retry workspace=%s action=%s error=%s",
                claim.workspace_id,
                claim.action,
                type(exc).__name__,
            )
            async with scoped() as db:
                await leases.fail_maintenance(db, claim)
                await db.commit()


async def maintain_goal_sandboxes(*, maker: Callable[[], AsyncSession] | None = None) -> None:
    # Read only identity pairs of workspaces needing attention at platform scope,
    # so an installation using only per-call sandboxes does one empty lookup.
    listing_session = (
        platform_session("scheduler.retention_gc")
        if maker is None
        else platform_session("scheduler.retention_gc", maker=maker)
    )
    async with listing_session as listing:
        owners = (
            await listing.execute(
                select(GoalSandbox.tenant_id, GoalSandbox.org_id)
                .where(GoalSandbox.state.not_in(("deleted", "unresolved")))
                .distinct()
            )
        ).all()
    for tenant_id, org_id in owners:
        try:
            await _sweep_org(tenant_id, org_id, maker)
        except Exception:
            logger.exception("Workspace maintenance deferred for organization %s", org_id)
