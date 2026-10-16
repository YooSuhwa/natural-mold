"""D-03: skill rows across org boundaries and their lifecycle tuples (plan 8.4, 7.2.1)."""

from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import AsyncClient

from app.authz.resource_lifecycle import apply_lifecycle, on_resource_created
from app.authz.service import AuthzService
from app.models.organization import Organization
from app.skills.creation import TextSkillCreation
from tests.authz.skill_route_testkit import (
    ME,
    OwnerModelStore,
    _all_skill_grants,
    _enforce,
    _move_skill,
    _share,
    _skill,
    _triples,
    use_authz_fixture,  # noqa: F401 - pytest fixture (use_authz)
)
from tests.authz.unit_scope_fixture import (
    TEST_TENANT_ID,
    create_test_text_skill,
    unit_context,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_skills import OTHER_USER_ID, _skill_md


async def test_enforce_hides_fully_granted_skill_of_another_org(client: AsyncClient, use_authz):
    skill_id = await _skill(OTHER_USER_ID)
    other_org = uuid4()
    async with TestSession() as db:
        db.add(Organization(id=other_org, tenant_id=TEST_TENANT_ID, name="Other", slug="other"))
        await db.commit()
    await _move_skill(skill_id, org_id=other_org)
    await _share(skill_id, org=other_org)  # granted in the DB and in OpenFGA alike
    use_authz.append(_enforce(*_all_skill_grants(f"skill:{skill_id}")))
    detail = await client.get(f"/api/skills/{skill_id}")
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "SKILL_NOT_FOUND"
    assert (await client.get("/api/skills")).json() == []


async def test_enforce_platform_skill_follows_the_allowed_org_answer(
    client: AsyncClient, use_authz
):
    # FakeAuthz stands for OpenFGA's computed answer: can_use/can_view on a platform
    # skill exist only through the org's allowed_org fan-out tuple (model.fga).
    skill_id = await _skill(name="platform")
    await _move_skill(skill_id, is_system=True, org_id=None, tenant_id=None)
    obj = f"skill:{skill_id}"
    use_authz.append(_enforce())
    assert (await client.get(f"/api/skills/{skill_id}")).status_code == 404
    assert (await client.get("/api/skills")).json() == []
    use_authz[0] = _enforce(("can_use", obj), ("can_view", obj))
    assert (await client.get(f"/api/skills/{skill_id}")).status_code == 200
    assert [row["id"] for row in (await client.get("/api/skills")).json()] == [str(skill_id)]


async def test_enforce_create_then_get_reads_its_own_write(
    client: AsyncClient, use_authz, monkeypatch
):
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    store = OwnerModelStore()
    use_authz.append(AuthzService(store, mode="enforce"))
    body = {"name": "fresh", "description": "d", "content": _skill_md("fresh")}
    created = await client.post("/api/skills", json=body)
    assert created.status_code == 201
    skill_id = created.json()["id"]
    obj = f"skill:{skill_id}"
    assert (ME, "owner", obj) in _triples(store)
    assert (await client.get(f"/api/skills/{skill_id}")).status_code == 200
    assert (await client.delete(f"/api/skills/{skill_id}")).status_code == 204
    assert not [t for t in store.stored() if t.object == obj]


async def test_lifecycle_tuples_are_queued_not_written_before_apply():
    store = OwnerModelStore()
    context = unit_context(TEST_USER_ID)
    await _skill(name="seed")  # caller membership
    async with TestSession() as db:
        skill = await create_test_text_skill(
            db,
            TextSkillCreation(name="queued", slug=None, description="d", content=_skill_md("q")),
            context,
        )
        change = await on_resource_created(db, skill, mode="enforce")
        await db.commit()
    assert change.outbox_ids and store.stored() == set()
    run = await apply_lifecycle(
        change, AuthzService(store, mode="enforce"), writer=store, sessions=TestSession
    )
    assert run is not None
    assert (ME, "owner", f"skill:{skill.id}") in _triples(store)
    legacy = await on_resource_created(AsyncMock(), skill, mode="legacy")
    assert legacy.outbox_ids == ()
