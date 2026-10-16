"""16.3: GrantService boundary and model validation, conflicts, listing, audit, SDK shape."""

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz import grants as grants_module
from app.authz.client import _client_tuple
from app.authz.fake import FakeTupleStore
from app.authz.grants import GrantRequest, GrantTarget
from app.authz.tuples import RelationTuple
from app.exceptions import AppError
from app.models.audit_event import AuditEvent
from app.models.authz import AuthzOutbox, ResourceGrant
from tests.authz.grant_testkit import (
    Clock,
    World,
    agent_target,
    count,
    mcp_target,
    seed_world,
    service,
    system_item,
)
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse

CHAT: dict[str, JsonValue] = {"allowed_sources": ["CHAT"]}


def invalid_cases(world: World) -> list[tuple[str, GrantRequest]]:
    later = Clock().now + timedelta(days=1)
    member = world.member
    return [
        ("agent", GrantRequest("installer", "user", member)),  # no such relation on agents
        ("agent", GrantRequest("owner", "user", member)),  # not a grant relation
        ("agent", GrantRequest("viewer", "user", member, expires_at=later)),  # model: no expiry
        ("agent", GrantRequest("consumer", "user", member, expires_at=Clock().now)),  # expired
        ("agent", GrantRequest("viewer", "user", world.outsider)),  # other organization
        ("agent", GrantRequest("viewer", "group", uuid4())),  # missing/deleted group
        ("agent", GrantRequest("viewer", "organization", world.other_ctx.org_id)),
        ("mcp", GrantRequest("consumer", "agent", world.agent, grant_kind="delegation")),
        ("agent", GrantRequest("consumer", "agent", world.agent)),  # delegation kind required
        ("agent", GrantRequest("viewer", "user", member, grant_kind="migration")),
        ("agent", GrantRequest("viewer", "user", member, condition_name="nope")),
    ]


async def test_invalid_grants_are_refused_before_any_write(db: AsyncSession):
    world, clock = await seed_world(db), Clock()
    grants = service(FakeTupleStore(), clock)
    targets = {"agent": await agent_target(db, world), "mcp": await mcp_target(db, world)}
    for kind, request in invalid_cases(world):
        with pytest.raises(AppError) as error:
            await grants.grant(db, world.ctx, targets[kind], request)
        assert (error.value.code, error.value.status) == ("grant_invalid", 422), request
    archived = await agent_target(db, world, archived=True)
    with pytest.raises(AppError) as error:
        await grants.grant(db, world.ctx, archived, GrantRequest("viewer", "user", world.member))
    assert error.value.code == "grant_invalid"
    assert await count(db, ResourceGrant) == 0 and await count(db, AuthzOutbox) == 0


async def test_valid_principals_and_conditions_are_accepted(db: AsyncSession):
    world, clock = await seed_world(db), Clock()
    grants = service(FakeTupleStore(), clock)
    mcp = GrantRequest(
        "consumer",
        "agent",
        world.agent,
        grant_kind="delegation",
        condition_name="allowed_run_source",
        condition_context=CHAT,
    )
    expiring = GrantRequest("consumer", "user", world.member, expires_at=clock.now + timedelta(1))
    accepted = [
        (await agent_target(db, world), GrantRequest("editor", "group", world.group)),
        (await agent_target(db, world), GrantRequest("consumer", "organization", world.ctx.org_id)),
        (
            await agent_target(db, world),
            GrantRequest("consumer", "agent", world.agent, grant_kind="delegation"),
        ),
        (await mcp_target(db, world), mcp),
        (await agent_target(db, world), expiring),
    ]
    for target, request in accepted:
        await grants.grant(db, world.ctx, target, request)
    await db.commit()
    assert await count(db, ResourceGrant) == await count(db, AuthzOutbox) == len(accepted)


async def test_targets_are_resolved_from_the_database(db: AsyncSession):
    world, clock = await seed_world(db), Clock()
    grants = service(FakeTupleStore(), clock)
    request = GrantRequest("viewer", "user", world.member)
    foreign = await agent_target(db, world, ctx=world.other_ctx)
    for target in (foreign, GrantTarget("agent", uuid4()), GrantTarget("workflow", uuid4())):
        with pytest.raises(AppError) as error:
            await grants.grant(db, world.ctx, target, request)
        assert error.value.status == 404


async def test_another_organizations_grant_on_the_same_key_is_never_updated(db: AsyncSession):
    world, clock = await seed_world(db), Clock()
    grants = service(FakeTupleStore(), clock)
    item = GrantTarget("marketplace_item", await system_item(db))
    actor = GrantRequest("installer", "user", world.ctx.user_id)  # a member of both orgs
    await grants.grant(db, world.ctx, item, actor)
    await db.commit()
    with pytest.raises(AppError) as error:
        await grants.grant(db, world.other_ctx, item, actor)
    assert error.value.code == "conflict"
    await db.rollback()
    [grant] = (await db.scalars(select(ResourceGrant))).all()
    assert grant.org_id == world.ctx.org_id


async def test_concurrent_first_share_is_a_conflict(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    world, clock = await seed_world(db), Clock()
    grants, target = service(FakeTupleStore(), clock), await agent_target(db, world)
    request = GrantRequest("viewer", "user", world.member)
    await grants.grant(db, world.ctx, target, request)
    await db.commit()

    async def unseen(*args: object) -> None:  # the other transaction's insert is invisible
        return None

    monkeypatch.setattr(grants_module, "_active", unseen)
    with pytest.raises(AppError) as error:
        await grants.grant(db, world.ctx, target, request)
    assert error.value.code == "conflict"


async def test_revoke_of_unknown_or_foreign_grant_is_not_found(db: AsyncSession):
    world, clock = await seed_world(db), Clock()
    grants = service(FakeTupleStore(), clock)
    target = await agent_target(db, world)
    change = await grants.grant(db, world.ctx, target, GrantRequest("viewer", "user", world.member))
    await db.commit()
    for ctx, grant_id in ((world.ctx, uuid4()), (world.other_ctx, change.grant.id)):
        with pytest.raises(AppError) as error:
            await grants.revoke(db, ctx, grant_id)
        assert error.value.status == 404
    await grants.revoke(db, world.ctx, change.grant.id)
    await db.commit()
    with pytest.raises(AppError):  # already revoking
        await grants.revoke(db, world.ctx, change.grant.id)


async def test_list_returns_this_organizations_live_grants(db: AsyncSession):
    world, clock = await seed_world(db), Clock()
    grants, target = service(FakeTupleStore(), clock), await agent_target(db, world)
    kept = await grants.grant(db, world.ctx, target, GrantRequest("viewer", "user", world.member))
    gone = await grants.grant(db, world.ctx, target, GrantRequest("editor", "group", world.group))
    await db.commit()
    await grants.revoke(db, world.ctx, gone.grant.id)
    await db.commit()
    listed = await grants.list_grants(db, world.ctx, "agent", target.resource_id)
    assert {(g.id, g.state) for g in listed} == {
        (kept.grant.id, "active"),
        (gone.grant.id, "revoking"),
    }
    assert await grants.list_grants(db, world.other_ctx, "agent", target.resource_id) == []


async def test_grant_update_and_revoke_are_audited(db: AsyncSession):
    world, clock = await seed_world(db), Clock()
    grants, target = service(FakeTupleStore(), clock), await agent_target(db, world)
    plain = GrantRequest("consumer", "user", world.member)
    change = await grants.grant(db, world.ctx, target, plain)
    later = clock.tick()
    expiring = GrantRequest("consumer", "user", world.member, expires_at=later + timedelta(1))
    await grants.grant(db, world.ctx, target, expiring)
    await grants.revoke(db, world.ctx, change.grant.id)
    await db.commit()
    events = {e.action: e for e in (await db.scalars(select(AuditEvent))).all()}
    assert set(events) == {
        audit_actions.GRANT_CREATE,
        audit_actions.GRANT_UPDATE,
        audit_actions.GRANT_REVOKE,
    }
    for event in events.values():
        assert event.actor_user_id == world.ctx.user_id and event.outcome == "success"
        assert (event.target_type, event.target_id) == ("agent", str(target.resource_id))
        assert event.event_metadata and event.event_metadata["grant_id"] == str(change.grant.id)
    update = events[audit_actions.GRANT_UPDATE].event_metadata or {}
    assert update["before"] == {"expires_at": None, "condition": None, "context": None}
    assert update["expires_at"] is not None


def test_sdk_tuple_keeps_condition_on_write_and_drops_it_on_delete():
    item = RelationTuple.of("user:u", "consumer", "agent:a", "non_expired_grant", {"x": 1})
    written = _client_tuple(item, with_condition=True)
    assert written.condition is not None and written.condition.name == "non_expired_grant"
    assert written.condition.context == {"x": 1}
    assert _client_tuple(item, with_condition=False).condition is None
