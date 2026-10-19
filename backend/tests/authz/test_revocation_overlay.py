"""16.4 / D12 / 8.6: a pending revocation marker denies even while OpenFGA still allows."""

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.authz.client import AuthzUnavailable
from app.authz.org_context import OrgContext
from app.authz.pending_markers import PendingMarkers
from app.authz.principal import Principal
from app.authz.revocation_overlay import DbRevocationOverlay
from app.authz.revocations import ANY_SUBJECT, finalize_revocations, record_revocation
from app.authz.service import AuthzService, ShadowDecision, ShadowSample, Verdict
from app.exceptions import AppError
from tests.authz.authz_service_testkit import (
    AGENT,
    ORG,
    OTHER_ORG,
    USER,
    RecordingClient,
    grants,
    require,
    user,
)
from tests.authz.revocation_testkit import (
    SUBJECT,
    FakeClock,
    _fresh_pending_hint,  # noqa: F401 - autouse fixture
    overlaid,
    revoke,
    seed_org,
)

OBJECT_SCOPES = ["grant", "ownership", "resource"]
ORG_SCOPES = ["membership", "group_member", "role", "capability"]


def _stale() -> RecordingClient:
    """OpenFGA has not applied the delete yet: the tuple still allows."""
    return RecordingClient(grants("can_run", "can_edit"))


async def _status(svc: AuthzService) -> int | None:
    try:
        await require(svc)
    except AppError as error:
        return error.status
    return None


@pytest.mark.parametrize("scope", OBJECT_SCOPES + ORG_SCOPES)
async def test_committed_marker_denies_stale_allow_until_finalized(db: AsyncSession, scope):
    tenant = await seed_org(db, ORG)
    svc = overlaid(_stale())
    assert await _status(svc) is None
    obj = AGENT if scope in OBJECT_SCOPES else f"group:{uuid4()}"
    marker = await revoke(db, ORG, tenant, scope, obj)
    # OpenFGA delete failed / not yet applied: still denied (404, never a 403 oracle).
    assert await _status(svc) == 404
    await finalize_revocations(db, [marker])
    await db.commit()
    assert await _status(svc) is None


async def test_reconcile_rewriting_the_tuple_cannot_lift_a_pending_marker(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    await revoke(db, ORG, tenant, "grant", AGENT)
    client = RecordingClient()  # the delete reached OpenFGA ...
    svc = overlaid(client)
    assert await _status(svc) == 404
    client.allowed = grants("can_run", "can_edit")  # ... and something re-wrote it
    assert await _status(svc) == 404


async def test_markers_never_cross_organizations_or_other_users(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    await seed_org(db, OTHER_ORG)
    await revoke(db, ORG, tenant, "grant", AGENT)
    await revoke(db, ORG, tenant, "membership", f"organization:{ORG}", subject=f"user:{uuid4()}")
    client = RecordingClient(grants("can_run", "can_edit"))
    svc = overlaid(client)
    foreign = Principal(kind="user", id=USER, org_id=OTHER_ORG)
    assert await svc.check(foreign, "can_run", AGENT)
    other_user = Principal(kind="user", id=uuid4(), org_id=ORG)
    client.allowed |= {(other_user.fga(), "can_run", AGENT)}
    assert await svc.check(other_user, "can_run", AGENT)
    assert not await svc.check(user(), "can_run", AGENT)


@pytest.mark.parametrize(
    "subject",
    [ANY_SUBJECT, f"group:{uuid4()}", f"organization:{ORG}"],
    ids=["any", "group", "organization"],
)
async def test_non_actor_subject_blocks_the_object_for_everyone(db: AsyncSession, subject):
    tenant = await seed_org(db, ORG)
    await revoke(db, ORG, tenant, "resource" if subject == ANY_SUBJECT else "grant", AGENT, subject)
    member = Principal(kind="user", id=uuid4(), org_id=ORG)
    svc = overlaid(RecordingClient(frozenset({(member.fga(), "can_run", AGENT)})))
    assert not await svc.check(member, "can_run", AGENT)


async def test_org_wide_marker_blocks_every_decision_of_the_subject(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    other = f"agent:{uuid4()}"
    cap = (SUBJECT, "cap_create_agent", f"organization:{ORG}")
    client = RecordingClient(grants("can_run") | grants("can_run", obj=other) | {cap})
    svc = overlaid(client)
    await revoke(db, ORG, tenant, "group_member", f"group:{uuid4()}")
    assert await svc.batch_check(user(), [("can_run", AGENT), ("can_run", other)]) == {
        ("can_run", AGENT): False,
        ("can_run", other): False,
    }
    assert await svc.list_objects(user(), "can_run", "agent") == set()
    ctx = OrgContext(user_id=USER, org_id=ORG, tenant_id=tenant)
    with pytest.raises(AppError) as caught:
        await svc.require_cap(ctx, "cap_create_agent", legacy=True)
    assert caught.value.status == 403


async def test_list_paths_drop_only_revoked_objects(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    other = f"agent:{uuid4()}"
    svc = overlaid(RecordingClient(grants("can_run", "can_edit") | grants("can_run", obj=other)))
    await revoke(db, ORG, tenant, "grant", AGENT)
    assert await svc.list_objects(user(), "can_run", "agent") == {other}
    assert await svc.filter_visible(user(), "can_run", [AGENT, other], legacy=[]) == [other]
    assert await svc.my_permissions(user(), AGENT, ["can_run", "can_edit"], legacy=[]) == []


async def test_marker_storage_failure_fails_closed():
    broken = create_async_engine("sqlite+aiosqlite://")  # no authz_revocations table
    try:
        overlay = DbRevocationOverlay(async_sessionmaker(broken), hint=PendingMarkers())
        svc = AuthzService(_stale(), mode="enforce", overlay=overlay)
        assert await _status(svc) == 503
        with pytest.raises(AppError) as caught:
            await svc.filter_visible(user(), "can_run", [AGENT], legacy=[])
        assert caught.value.status == 503
    finally:
        await broken.dispose()


async def test_legacy_mode_never_consults_the_overlay(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    await revoke(db, ORG, tenant, "grant", AGENT)
    svc = overlaid(_stale(), "legacy")
    assert await _status(svc) is None  # legacy verdict ALLOW stands


async def test_shadow_mode_answers_legacy_and_records_the_overlaid_sample(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    await revoke(db, ORG, tenant, "grant", AGENT)
    samples: list[ShadowSample] = []
    svc = overlaid(_stale(), "shadow", hook=samples.append)
    assert await _status(svc) is None
    (sample,) = samples
    # The C-06 recorder re-decides the sample: the overlay applies and is flagged.
    assert await svc.shadow_decide(sample) == ShadowDecision(Verdict.NOT_FOUND, True)


async def test_shadow_decision_flags_only_overlay_vetoes(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    allow = ShadowSample(user(), "can_edit", AGENT, "can_run", ORG, Verdict.ALLOW)
    svc = overlaid(_stale(), "shadow")
    assert await svc.shadow_decide(allow) == ShadowDecision(Verdict.ALLOW, False)
    denied = overlaid(RecordingClient(grants("can_run")), "shadow")
    assert await denied.shadow_decide(allow) == ShadowDecision(Verdict.FORBIDDEN, False)
    foreign = ShadowSample(user(), "can_edit", AGENT, "can_run", OTHER_ORG, Verdict.ALLOW)
    assert await svc.shadow_decide(foreign) == ShadowDecision(Verdict.NOT_FOUND, False)
    cap = ShadowSample(user(), "can_edit", AGENT, None, ORG, Verdict.ALLOW)
    await revoke(db, ORG, tenant, "role", f"organization:{ORG}")
    assert await svc.shadow_decide(cap) == ShadowDecision(Verdict.FORBIDDEN, True)


async def test_agent_acting_for_a_user_is_denied_during_the_users_revocation(db: AsyncSession):
    tenant = await seed_org(db, ORG)
    agent = Principal(kind="agent", id=uuid4(), org_id=ORG, acting_user_id=USER)
    unrelated = Principal(kind="agent", id=uuid4(), org_id=ORG, acting_user_id=uuid4())
    client = RecordingClient(
        frozenset({(agent.fga(), "can_run", AGENT), (unrelated.fga(), "can_run", AGENT)})
    )
    svc = overlaid(client)
    assert await svc.check(agent, "can_run", AGENT)
    await revoke(db, ORG, tenant, "membership", f"organization:{ORG}")  # subject user:USER
    assert not await svc.check(agent, "can_run", AGENT)
    assert await svc.check(unrelated, "can_run", AGENT)


@pytest.mark.parametrize("failure", [OSError("reset"), TimeoutError()], ids=["os", "timeout"])
async def test_transport_failures_fail_closed(failure):
    @asynccontextmanager
    async def sessions():
        raise failure
        yield

    overlay = DbRevocationOverlay(sessions, hint=PendingMarkers())
    with pytest.raises(AuthzUnavailable, match="revocation_overlay"):
        await overlay.blocked(user(), [AGENT])


async def test_commit_refreshes_the_pending_hint(db: AsyncSession, monkeypatch):
    tenant = await seed_org(db, ORG)
    clock = FakeClock()
    hint = PendingMarkers(ttl=1.0, clock=clock)
    monkeypatch.setattr("app.authz.pending_markers.pending_markers", hint)
    await record_revocation(
        db, org_id=ORG, tenant_id=tenant, scope="grant", subject=SUBJECT, object=AGENT
    )
    clock.now += 0.9  # a slow transaction ...
    await db.commit()  # ... commits: lookups are forced for a full ttl from here
    clock.now += 0.6
    assert await hint.any_pending(_zero) is True


async def test_pending_count_is_cached_for_one_second(db: AsyncSession, monkeypatch):
    tenant = await seed_org(db, ORG)
    clock = FakeClock()
    hint = PendingMarkers(ttl=1.0, clock=clock)
    monkeypatch.setattr("app.authz.pending_markers.pending_markers", hint)
    svc = overlaid(_stale(), hint=hint)
    assert await svc.check(user(), "can_run", AGENT)  # caches "0 pending"
    await revoke(db, ORG, tenant, "grant", AGENT)  # recorded in this process
    assert not await svc.check(user(), "can_run", AGENT)  # own marker: no stale zero
    hint.reset()
    assert await hint.any_pending(_zero) is False  # another process: cached zero ...
    assert await svc.check(user(), "can_run", AGENT)  # ... bounded stale window
    clock.now += 1.0
    assert not await svc.check(user(), "can_run", AGENT)  # re-read after ttl


async def _zero() -> int:
    return 0


async def test_failed_count_read_is_not_cached():
    hint = PendingMarkers()

    async def broken() -> int:
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await hint.any_pending(broken)
    assert await hint.any_pending(_zero) is False
