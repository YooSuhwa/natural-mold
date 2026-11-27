"""Run a profiled skill within the current goal's private intermediate directory."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from opensandbox import Sandbox
from opensandbox.exceptions import SandboxException

from app.agent_runtime import goal_sandbox_provider as provider
from app.agent_runtime import skill_sandbox
from app.agent_runtime.skill_sandbox import SkillRunResult, SkillSandboxError
from app.config import settings
from app.database import SessionTenant, tenant_session
from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.services import goal_sandbox_leases as leases
from app.services.cancel_deferred import finish_then_cancel
from app.services.goal_sandbox_artifacts import check_goal_output_destinations, index_goal_outputs
from app.services.goal_sandbox_policy import resolve_goal_sandbox_scope
from app.services.goal_sandbox_profile import skill_tree_digest

logger = logging.getLogger(__name__)


async def run_if_eligible(
    ctx: SkillToolContext,
    descriptor: SkillRuntimeDescriptor,
    args: list[str],
    *,
    skill_dir: Path,
    env: dict[str, str],
    timeout_seconds: float,
) -> SkillRunResult | None:
    # These calls have no new side effects or DB work at all in ordinary deployments.
    if (
        not settings.goal_sandbox_enabled
        or not ctx.goal_sandbox_run_id
        or descriptor.credential_bindings
        or (descriptor.execution_profile or {}).get("requires_network")
        or set(env) - {"PYTHONPATH", "HOME", "SKILL_OUTPUT_DIR", "OUTPUTS_DIR"}
    ):
        return None
    files = await asyncio.to_thread(
        skill_sandbox.read_skill_tree, skill_dir, settings.skill_max_package_bytes
    )
    digest = skill_tree_digest(files)
    actor = SessionTenant(ctx.tenant_id, ctx.user_id)
    async with tenant_session(actor) as db:
        scope = await resolve_goal_sandbox_scope(db, ctx, descriptor, skill_hash=digest)
        if scope is None:
            return None
        claim = await leases.acquire(db, scope, ttl=timedelta(seconds=timeout_seconds + 240))
        if claim is None:
            raise SkillSandboxError("this goal workspace is currently in use.")
        await db.commit()

    sandbox: Sandbox | None = None
    released = False
    try:
        sandbox = await provider.open_workspace(claim, timeout_seconds)
        # A create/attach can block. Verify the same goal, policy and permission
        # after provisioning and immediately before dispatching the command.
        async with tenant_session(actor) as db:
            authorized = await resolve_goal_sandbox_scope(db, ctx, descriptor, skill_hash=digest)
            if authorized != scope:
                raise SkillSandboxError("goal workspace authorization or lease changed.")
            if claim.action == "create":
                attached = await leases.bind_provider(db, claim, sandbox.id)
            else:
                attached = claim.provider_id == sandbox.id and await leases.lock_writer(db, claim)
            if not attached:
                raise SkillSandboxError("goal workspace authorization or lease changed.")
            await db.commit()
        await provider.prepare_workspace(
            sandbox, files, new=claim.action == "create", skill_hash=digest
        )
        async with tenant_session(actor) as db:
            authorized = await resolve_goal_sandbox_scope(db, ctx, descriptor, skill_hash=digest)
            if authorized != scope or not await leases.lock_writer(db, claim):
                raise SkillSandboxError("goal workspace authorization changed before execution.")
            await db.commit()
        run = await skill_sandbox.run_command(
            sandbox, args, {**env, "MOLDY_GOAL_WORK_DIR": provider.WORK_DIR}, timeout_seconds
        )
        if run.returncode == 0 and not run.timed_out:
            outputs = await provider.collect_outputs(sandbox)
            manifest = provider.output_manifest(outputs)
            async with tenant_session(actor) as db:
                if not await leases.record_outputs_ready(
                    db, claim, manifest=manifest, run_id=UUID(ctx.goal_sandbox_run_id)
                ):
                    raise SkillSandboxError(
                        "goal workspace lease expired before output collection."
                    )
                await db.commit()
            async with tenant_session(actor) as db:
                authorized = await resolve_goal_sandbox_scope(
                    db, ctx, descriptor, skill_hash=digest, in_flight=scope
                )
                if authorized != scope or not await leases.lock_writer(db, claim):
                    raise SkillSandboxError(
                        "goal workspace authorization changed before publishing."
                    )
                await check_goal_output_destinations(
                    db, scope=scope, run_id=UUID(ctx.goal_sandbox_run_id), manifest=manifest
                )
                await finish_then_cancel(
                    asyncio.to_thread(provider.publish_outputs, ctx.output_dir, outputs)
                )
                await index_goal_outputs(
                    db,
                    scope=scope,
                    run_id=UUID(ctx.goal_sandbox_run_id),
                    directory=ctx.output_dir,
                    manifest=manifest,
                )
                if not await leases.finish(db, claim, manifest=manifest):
                    raise SkillSandboxError("goal workspace lease expired before publication.")
                await db.commit()
                released = True
        return SkillRunResult(
            run.stdout, run.stderr, run.returncode, run.timed_out, sandbox_id=sandbox.id
        )
    except SandboxException as exc:
        logger.warning("goal sandbox request failed: %s", type(exc).__name__)
        raise SkillSandboxError("goal workspace is unavailable.") from exc
    finally:
        try:
            if not released:
                await finish_then_cancel(_abandon(actor, claim))
        finally:
            if sandbox is not None:
                await finish_then_cancel(sandbox.close())


async def _abandon(actor: SessionTenant, claim: leases.SandboxClaim) -> None:
    async with tenant_session(actor) as db:
        await leases.abandon(db, claim)
        await db.commit()
