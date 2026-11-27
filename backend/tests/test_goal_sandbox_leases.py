"""Deterministic lease expiry, publication and cleanup recovery cases."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.migrations.org_authz_v1.contracts import ORG, TENANT
from app.models.goal_sandbox import GoalSandbox
from app.services import goal_sandbox_leases as leases
from tests.authz.registration_fixture import seed_registration_default

START = datetime(2026, 1, 1, tzinfo=UTC)
STEP = timedelta(minutes=2)
MANIFEST = {"output.bin": "a" * 64}


@pytest.fixture
async def scope(db: AsyncSession) -> leases.SandboxScope:
    await seed_registration_default(db)
    return leases.SandboxScope(
        tenant_id=TENANT,
        org_id=ORG,
        user_id=uuid4(),
        conversation_id=uuid4(),
        goal_id=uuid4(),
        goal_version=1,
        agent_id=uuid4(),
        skill_id=uuid4(),
        policy_version=1,
        policy_hash="1" * 64,
        skill_hash="2" * 64,
        image="sha256:123",
    )


async def _start(db: AsyncSession, scope: leases.SandboxScope) -> leases.SandboxClaim:
    claim = await leases.acquire(db, scope, ttl=STEP, now=START)
    assert claim is not None and claim.action == "create"
    assert await leases.bind_provider(db, claim, "provider-test", now=START)
    return claim


async def _maintenance(
    db: AsyncSession,
    scope: leases.SandboxScope,
    step: int,
    *,
    disabled: bool = False,
) -> leases.SandboxClaim | None:
    now = START + step * STEP
    return await leases.claim_maintenance(
        db,
        scope.tenant_id,
        org_id=scope.org_id,
        ttl=STEP,
        idle_before=now - STEP,
        expire_before=now - 4 * STEP,
        disabled=disabled,
        now=now,
    )


async def test_unknown_writer_never_reenters_sandbox_even_after_it_expires(
    db: AsyncSession, scope: leases.SandboxScope
) -> None:
    expired = await _start(db, scope)
    assert not await leases.finish(db, expired, manifest={}, now=START)
    assert not await leases.record_outputs_ready(db, expired, manifest={}, now=START + STEP)
    assert await leases.acquire(db, scope, ttl=STEP, now=START + STEP) is None
    recovery = await _maintenance(db, scope, 1)
    assert recovery and recovery.action == "recover" and recovery.pending_manifest is None
    assert not await leases.lock_writer(db, expired, now=START + STEP)
    assert not await leases.complete_maintenance(db, recovery, manifest={}, now=START + STEP)
    assert await leases.mark_unresolved(db, recovery, "unconfirmed_writer", now=START + STEP)
    assert await _maintenance(db, scope, 9) is None
    assert await leases.acquire(db, scope, ttl=STEP, now=START + 9 * STEP) is None
    row = await db.get(GoalSandbox, expired.workspace_id)
    assert row and row.state == "unresolved" and row.resolution_reason == "unconfirmed_writer"


async def test_crashed_publication_must_recover_exact_staged_hash_before_delete(
    db: AsyncSession, scope: leases.SandboxScope
) -> None:
    run = uuid4()
    claim = await _start(db, scope)
    assert await leases.record_outputs_ready(db, claim, manifest=MANIFEST, run_id=run, now=START)
    assert await leases.record_outputs_ready(db, claim, manifest=MANIFEST, run_id=run, now=START)
    assert not await leases.record_outputs_ready(db, claim, manifest={}, now=START)
    assert not await leases.finish(db, claim, manifest={}, now=START)
    recovery = await _maintenance(db, scope, 2)
    assert recovery and recovery.action == "recover" and recovery.pending_manifest == MANIFEST
    assert recovery.output_run_id == run
    assert not await leases.complete_maintenance(db, recovery, manifest={}, now=START + 2 * STEP)
    assert await leases.complete_maintenance(db, recovery, manifest=MANIFEST, now=START + 2 * STEP)
    deleting = await _maintenance(db, scope, 2)
    assert deleting and deleting.action == "delete" and deleting.manifest == MANIFEST
    assert deleting.output_run_id == run
    assert await leases.complete_maintenance(db, deleting, now=START + 2 * STEP)
    row = await db.get(GoalSandbox, claim.workspace_id)
    assert row and row.deleted_at is not None and row.receipt == MANIFEST
    fresh = await leases.acquire(db, scope, ttl=STEP, now=START + 2 * STEP)
    assert fresh and fresh.action == "create" and fresh.workspace_id != claim.workspace_id


async def test_pause_resume_keeps_provenance_and_new_attempt_stages_own_receipt(
    db: AsyncSession, scope: leases.SandboxScope
) -> None:
    first = await _start(db, scope)
    assert await leases.record_outputs_ready(db, first, manifest=MANIFEST, now=START)
    assert await leases.finish(db, first, manifest=MANIFEST, now=START)
    paused = await _maintenance(db, scope, 1)
    assert paused and paused.action == "pause" and paused.provider_id == "provider-test"
    assert await leases.complete_maintenance(db, paused, now=START + STEP)
    resumed = await leases.acquire(db, scope, ttl=STEP, now=START + STEP)
    assert resumed and resumed.action == "resume" and resumed.manifest == MANIFEST
    assert resumed.pending_manifest is None
    assert await leases.abandon(db, resumed, now=START + STEP)
    recovery = await _maintenance(db, scope, 1)
    assert recovery and recovery.pending_manifest is None
    assert not await leases.complete_maintenance(db, recovery, manifest=MANIFEST, now=START + STEP)
    assert await leases.retire_erased_scope(db, recovery, now=START + STEP)
    deletion = await _maintenance(db, scope, 1)
    assert deletion and deletion.action == "delete"


async def test_parallel_readers_delay_disabled_workspace_retirement_and_wrong_scope_cannot_release(
    db: AsyncSession, scope: leases.SandboxScope
) -> None:
    writer = await _start(db, scope)
    first = await leases.acquire_read(db, scope, ttl=STEP * 3, now=START)
    assert first is not None
    party = {
        "tenant_id": scope.tenant_id,
        "org_id": scope.org_id,
        "user_id": scope.user_id,
        "conversation_id": scope.conversation_id,
    }
    second = await leases.acquire_conversation_reads(db, **party, ttl=STEP, now=START)
    assert len(second) == 1
    assert await leases.record_outputs_ready(db, writer, manifest={}, now=START)
    assert await leases.finish(db, writer, manifest={}, now=START)
    assert not await leases.release_read(db, replace(scope, goal_id=uuid4()), first, now=START)
    assert (
        await leases.release_conversation_reads(
            db, **{**party, "user_id": uuid4()}, tokens=second, now=START
        )
        == 0
    )
    assert (
        await leases.renew_conversation_reads(db, **party, tokens=second, ttl=STEP * 4, now=START)
        == 1
    )
    assert await leases.release_read(db, scope, first, now=START)
    assert await _maintenance(db, scope, 2, disabled=True) is None
    assert (
        await leases.release_conversation_reads(db, **party, tokens=second, now=START + 2 * STEP)
        == 1
    )
    claim = await _maintenance(db, scope, 2, disabled=True)
    assert claim and claim.action == "delete"


async def test_leases_are_clock_bounded_and_scope_is_immutable(
    db: AsyncSession, scope: leases.SandboxScope
) -> None:
    writer = await _start(db, scope)
    for changed in (
        replace(scope, image="new"),
        replace(scope, policy_version=2),
        replace(scope, skill_hash="c" * 64),
    ):
        assert changed.fingerprint != scope.fingerprint
        wrong_scope = replace(writer, scope=changed)
        assert not await leases.lock_operation(db, wrong_scope, now=START)
    with pytest.raises(ValueError, match="invalid sandbox output"):
        await leases.record_outputs_ready(db, writer, manifest={"../other": "0" * 64}, now=START)
    assert not await leases.bind_provider(db, writer, "different-provider", now=START)
    assert await leases.record_outputs_ready(db, writer, manifest={}, now=START)
    assert await leases.finish(db, writer, manifest={}, now=START)
    claim = await _maintenance(db, scope, 1)
    assert claim and claim.action == "pause"
    assert await leases.fail_maintenance(db, claim, now=START + STEP)
    assert await _maintenance(db, scope, 1) is None
    deleting = await _maintenance(db, scope, 2)
    assert deleting and deleting.action == "delete"


async def test_idle_pause_does_not_extend_last_use_retention(
    db: AsyncSession, scope: leases.SandboxScope
) -> None:
    writer = await _start(db, scope)
    assert await leases.record_outputs_ready(db, writer, manifest={}, now=START)
    assert await leases.finish(db, writer, manifest={}, now=START)
    pause = await _maintenance(db, scope, 2)
    assert pause and await leases.complete_maintenance(db, pause, now=START + 2 * STEP)
    expired = await _maintenance(db, scope, 4)
    assert expired and expired.action == "delete"
