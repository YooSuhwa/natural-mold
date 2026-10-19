"""D-03: skill-family routes decide through ``AuthzService`` (plan 9.4, 8.4).

Legacy byte-identity is covered by the unchanged skill API suites (the legacy
service never reaches a client). These tests swap ``get_authz`` for shadow and
enforce services over ``FakeAuthz``-style clients.
"""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient

from app.authz.policies import ResourcePolicy, policy_of
from app.authz.service import AuthzService, ShadowSample, Verdict
from app.authz.skill_access import authorize_builder_target
from app.exceptions import AppError
from app.models.skill import Skill
from app.services import skill_revision_service
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.skill_route_testkit import (
    ORG,
    SKILL_GETS,
    _enforce,
    _share,
    _skill,
    use_authz_fixture,  # noqa: F401 - pytest fixture (use_authz)
)
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    unit_context,
)
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_skills import OTHER_USER_ID, _skill_md


@pytest.mark.parametrize("suffix", SKILL_GETS)
async def test_enforce_hides_ungranted_skill_as_existing_404(
    client: AsyncClient, use_authz, suffix
):
    skill_id = await _skill(OTHER_USER_ID)
    use_authz.append(_enforce())
    response = await client.get(f"/api/skills/{skill_id}{suffix}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SKILL_NOT_FOUND"


async def test_enforce_shows_other_owners_skill_when_granted(client: AsyncClient, use_authz):
    skill_id = await _skill(OTHER_USER_ID)
    await _share(skill_id)
    obj = f"skill:{skill_id}"
    use_authz.append(_enforce(("can_use", obj), ("can_view", obj)))
    assert (await client.get(f"/api/skills/{skill_id}")).status_code == 200
    listed = await client.get("/api/skills")
    assert [row["id"] for row in listed.json()] == [str(skill_id)]


async def test_enforce_use_only_forbids_view_and_edit(client: AsyncClient, use_authz):
    skill_id = await _skill(OTHER_USER_ID)
    await _share(skill_id)
    use_authz.append(_enforce(("can_use", f"skill:{skill_id}")))
    detail = await client.get(f"/api/skills/{skill_id}")
    assert detail.status_code == 403
    assert detail.json()["error"]["code"] == "permission_required"
    assert detail.json()["error"]["details"]["required"] == "can_view"
    patch = await client.patch(f"/api/skills/{skill_id}", json={"name": "x"})
    assert patch.json()["error"]["details"]["required"] == "can_edit"
    delete = await client.delete(f"/api/skills/{skill_id}")
    assert delete.json()["error"]["details"]["required"] == "can_delete"
    assert (await client.get(f"/api/skills/{skill_id}/feedback")).status_code == 200


async def test_enforce_answers_by_openfga_even_for_own_skill(client: AsyncClient, use_authz):
    skill_id = await _skill()
    use_authz.append(_enforce())
    assert (await client.get(f"/api/skills/{skill_id}")).status_code == 404
    assert (await client.get("/api/skills")).json() == []


async def test_enforce_fails_closed_when_openfga_is_down(client: AsyncClient, use_authz):
    skill_id = await _skill()
    use_authz.append(_enforce(unavailable=True))
    response = await client.get(f"/api/skills/{skill_id}/revisions")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "authz_unavailable"


async def test_enforce_create_requires_capability(client: AsyncClient, use_authz):
    body = {"name": "new", "description": "d", "content": _skill_md("new")}
    use_authz.append(_enforce())
    denied = await client.post("/api/skills", json=body)
    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"capability": "cap_create_skill"}
    upload = await client.post("/api/skills/upload", files={"file": ("a.skill", b"x")})
    assert upload.json()["error"]["code"] == "capability_required"
    use_authz[0] = _enforce(("cap_create_skill", ORG))
    assert (await client.post("/api/skills", json=body)).status_code == 201


async def test_enforce_is_per_type(client: AsyncClient, use_authz):
    skill_id = await _skill()
    service = AuthzService(RecordingClient(), mode="enforce", enforce_types=frozenset({"agent"}))
    use_authz.append(service)
    assert (await client.get(f"/api/skills/{skill_id}")).status_code == 200
    assert service._client.queries == []  # type: ignore[attr-defined]  # noqa: SLF001


async def test_shadow_answers_legacy_and_samples_each_decision(client: AsyncClient, use_authz):
    mine = await _skill()
    theirs = await _skill(OTHER_USER_ID, name="theirs")
    await _share(theirs)
    samples: list[ShadowSample] = []
    client_ = RecordingClient()
    use_authz.append(AuthzService(client_, mode="shadow", shadow_hook=samples.append))

    assert (await client.get(f"/api/skills/{mine}")).status_code == 200
    assert (await client.get(f"/api/skills/{theirs}")).status_code == 404
    listed = await client.get("/api/skills")

    assert [row["id"] for row in listed.json()] == [str(mine)]
    assert client_.queries == []  # decided off the request path by the recorder
    detail = [(s.obj, s.permission, s.legacy, s.resource_org_id) for s in samples[:2]]
    assert detail == [
        (f"skill:{mine}", "can_view", Verdict.ALLOW, TEST_ORG_ID),
        (f"skill:{theirs}", "can_view", Verdict.NOT_FOUND, TEST_ORG_ID),
    ]
    assert {(s.obj, s.legacy) for s in samples[2:]} == {
        (f"skill:{mine}", Verdict.ALLOW),
        (f"skill:{theirs}", Verdict.NOT_FOUND),
    }


async def test_builder_target_needs_create_cap_or_source_edit():
    await _skill(name="mine")  # seeds the caller's membership
    source = await _skill(OTHER_USER_ID, name="source")
    await _share(source)
    context = unit_context(TEST_USER_ID)
    legacy = AuthzService(RecordingClient(unavailable=True), mode="legacy")
    async with TestSession() as db:
        await authorize_builder_target(db, legacy, context, None)
        await authorize_builder_target(db, legacy, context, source)  # service keeps deciding
        with pytest.raises(AppError) as cap:
            await authorize_builder_target(db, _enforce(), context, None)
        assert cap.value.code == "capability_required"
        viewer = _enforce(("can_use", f"skill:{source}"), ("can_view", f"skill:{source}"))
        with pytest.raises(AppError) as edit:
            await authorize_builder_target(db, viewer, context, source)
        assert edit.value.details == {"required": "can_edit", "request_access": True}
        editor = _enforce(("can_use", f"skill:{source}"), ("can_edit", f"skill:{source}"))
        await authorize_builder_target(db, editor, context, source)


def test_all_47_skill_family_routes_declare_a_reviewed_policy(test_app: FastAPI):
    routes = [
        route
        for route in test_app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith(("/api/skills", "/api/skill-builder"))
    ]
    policies = [policy_of(route.endpoint) for route in routes]
    assert len(routes) == 47
    kinds = sorted(policy.kind for policy in policies if policy is not None)
    assert len(kinds) == 47 and "LEGACY" not in kinds
    assert {k: kinds.count(k) for k in set(kinds)} == {
        "RES": 36,
        "CANDIDATES": 1,
        "CAP": 2,
        "PRIVATE": 8,
    }
    resources = [p for p in policies if isinstance(p, ResourcePolicy)]
    assert {p.type_ for p in resources} == {"skill"}
    assert all(p.param == "skill_id" and p.min_visible == "can_use" for p in resources)


async def test_enforce_viewer_reads_another_owners_revision_history(client: AsyncClient, use_authz):
    skill_id = await _skill(OTHER_USER_ID)
    await _share(skill_id)
    async with TestSession() as db:
        skill = await db.get(Skill, skill_id)
        assert skill is not None
        revision = await skill_revision_service.create_revision_for_skill(
            db, skill=skill, user_id=OTHER_USER_ID, operation="create"
        )
        await db.commit()
    obj = f"skill:{skill_id}"
    use_authz.append(_enforce(("can_use", obj), ("can_view", obj)))
    listed = await client.get(f"/api/skills/{skill_id}/revisions")
    assert [row["id"] for row in listed.json()] == [str(revision.id)]
    detail = await client.get(f"/api/skills/{skill_id}/revisions/{revision.id}")
    assert detail.status_code == 200
    rollback = await client.post(f"/api/skills/{skill_id}/revisions/{revision.id}/rollback")
    assert rollback.json()["error"]["details"]["required"] == "can_edit"
