"""Workflow W1: the management API (owner only), drafts, checks, publishing, pins."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from app.models.agent import Agent
from app.models.audit_event import AuditEvent
from app.models.guardrail_template import AgentGuardrailBinding
from app.models.skill import AgentSkillLink, Skill
from app.models.workflow import Workflow, WorkflowVersion
from app.workflows.pins import PINNED_TABLES, UNPINNED_TABLES, agent_fingerprints
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, make_agent, make_model, seed_agent
from tests.workflow_fixtures import inquiry_workflow, node

BASE = "/api/workflows"


async def _create(client: AsyncClient, name: str = "문의 처리") -> dict[str, Any]:
    response = await client.post(BASE, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _save(client: AsyncClient, workflow: dict[str, Any], definition: Any) -> Any:
    return await client.put(
        f"{BASE}/{workflow['id']}/draft",
        json={"revision": workflow["draft_revision"], "definition": definition},
    )


async def _audits(db: AsyncSession, action: str) -> list[AuditEvent]:
    rows = await db.execute(sa.select(AuditEvent).where(AuditEvent.action == action))
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_create_starts_from_a_start_node_and_is_audited(
    client: AsyncClient, db: AsyncSession
) -> None:
    await seed_agent(db)
    await db.commit()
    created = await _create(client)
    assert created["draft_revision"] == 1 and created["published_version_no"] is None
    assert [n["type"] for n in created["draft_definition"]["nodes"]] == ["start"]
    listed = (await client.get(BASE)).json()
    assert [item["id"] for item in listed] == [created["id"]]
    (event,) = await _audits(db, "workflow.create")
    assert (event.target_type, event.target_id, event.outcome) == (
        "workflow",
        created["id"],
        "success",
    )
    assert event.target_name_snapshot == "문의 처리"


@pytest.mark.asyncio
async def test_a_stale_save_is_a_409_and_writes_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    created = await _create(client)
    first = await _save(client, created, inquiry_workflow(str(agent.id)))
    assert first.status_code == 200 and first.json()["draft_revision"] == 2
    stale = await _save(client, created, {"nodes": []})  # still says revision 1
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "WORKFLOW_DRAFT_STALE"
    current = (await client.get(f"{BASE}/{created['id']}")).json()
    assert current["draft_revision"] == 2 and len(current["draft_definition"]["nodes"]) == 7


@pytest.mark.asyncio
async def test_a_malformed_draft_is_a_422_listing_problems(
    client: AsyncClient, db: AsyncSession
) -> None:
    await seed_agent(db)
    await db.commit()
    created = await _create(client)
    response = await _save(client, created, {"nodes": [{"id": "x", "type": "loop"}]})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "WORKFLOW_DEFINITION_INVALID" and body["details"]["problems"]
    assert (await client.get(f"{BASE}/{created['id']}")).json()["draft_revision"] == 1


@pytest.mark.asyncio
async def test_issues_then_publish_pins_the_agent(client: AsyncClient, db: AsyncSession) -> None:
    _, _, agent = await seed_agent(db)
    await db.commit()
    created = await _create(client)
    issues = (await client.get(f"{BASE}/{created['id']}/issues")).json()
    assert {issue["code"] for issue in issues["issues"]} == {"needs_one_next", "no_end"}

    saved = (await _save(client, created, inquiry_workflow(str(agent.id)))).json()
    assert (await client.get(f"{BASE}/{created['id']}/issues")).json()["issues"] == []
    stale = await client.post(f"{BASE}/{created['id']}/publish", json={"revision": 1})
    assert stale.status_code == 409

    response = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved["draft_revision"]}
    )
    assert response.status_code == 201, response.text
    version = response.json()
    expected = (await agent_fingerprints(db, [agent.id]))[agent.id]
    assert version["version_no"] == 1
    assert version["agent_pins"] == {
        "classify": {"agent_id": str(agent.id), "fingerprint": expected}
    }
    assert (await client.get(f"{BASE}/{created['id']}")).json()["published_version_no"] == 1
    (event,) = await _audits(db, "workflow.publish")
    assert event.target_id == created["id"]
    assert event.event_metadata == {
        "version_no": 1,
        "definition_hash": version["definition_hash"],
        "agent_ids": [str(agent.id)],
    }

    again = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved["draft_revision"]}
    )
    assert again.json()["version_no"] == 2
    versions = (await client.get(f"{BASE}/{created['id']}/versions")).json()
    assert [v["version_no"] for v in versions] == [2, 1]
    one = await client.get(f"{BASE}/{created['id']}/versions/1")
    assert one.json()["definition"] == saved["draft_definition"]
    assert (await client.get(f"{BASE}/{created['id']}/versions/9")).status_code == 404


async def _publish_issues(client: AsyncClient, definition: dict[str, Any]) -> set[str]:
    created = await _create(client)
    saved = (await _save(client, created, definition)).json()
    response = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved["draft_revision"]}
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "WORKFLOW_CHECK_FAILED"
    assert not (await client.get(f"{BASE}/{created['id']}/versions")).json()
    return {issue["code"] for issue in error["details"]["issues"]}


@pytest.mark.asyncio
async def test_publish_refuses_agents_the_runner_cannot_use(
    client: AsyncClient, db: AsyncSession
) -> None:
    other = uuid.uuid4()
    _, model, _ = await seed_agent(db)
    await seed_agent(db, user_id=other)
    foreign = await make_agent(db, user_id=other, model_id=model.id, name="남의 것")
    archived = await make_agent(db, model_id=model.id, name="보관")
    archived.archived_at = sa.func.now()
    hidden = await make_agent(db, model_id=model.id, runtime_profile="skill_builder")
    no_tools = await make_model(db, model_name="no-tools", supports_function_calling=False)
    plain = await make_agent(db, model_id=no_tools.id, name="도구 없음")
    await db.commit()
    for agent, code in (
        (foreign, "agent_missing"),
        (hidden, "agent_missing"),
        (archived, "agent_archived"),
        (plain, "model_no_tools"),
    ):
        assert await _publish_issues(client, inquiry_workflow(str(agent.id))) == {code}
    assert await _publish_issues(client, inquiry_workflow(str(uuid.uuid4()))) == {"agent_missing"}
    broken = inquiry_workflow(str(uuid.uuid4()))
    node(broken, "review")["timeout_minutes"] = None
    assert await _publish_issues(client, broken) == {"agent_missing", "approval_no_timeout"}


@pytest.mark.asyncio
async def test_another_persons_workflow_is_not_found(client: AsyncClient, db: AsyncSession) -> None:
    other = uuid.uuid4()
    await seed_agent(db, user_id=other)
    theirs = Workflow(
        user_id=other,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        name="남의 워크플로",
        draft_definition={"nodes": []},
        draft_revision=1,
    )
    db.add(theirs)
    await db.commit()
    path = f"{BASE}/{theirs.id}"
    assert (await client.get(BASE)).json() == []
    for response in (
        await client.get(path),
        await client.put(f"{path}/draft", json={"revision": 1, "name": "x"}),
        await client.get(f"{path}/issues"),
        await client.post(f"{path}/publish", json={"revision": 1}),
        await client.get(f"{path}/versions"),
        await client.get(f"{path}/versions/1"),
        await client.post(f"{path}/archive"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"
    await db.refresh(theirs)
    assert (theirs.name, theirs.archived_at) == ("남의 워크플로", None)


@pytest.mark.asyncio
async def test_archive_hides_freezes_and_audits_once(client: AsyncClient, db: AsyncSession) -> None:
    await seed_agent(db)
    await db.commit()
    created = await _create(client)
    for _ in range(2):
        archived = await client.post(f"{BASE}/{created['id']}/archive")
        assert archived.status_code == 200 and archived.json()["archived_at"]
    assert (await client.get(BASE)).json() == []
    assert len((await client.get(BASE, params={"include_archived": True})).json()) == 1
    blocked = await client.put(
        f"{BASE}/{created['id']}/draft", json={"revision": 1, "name": "다시"}
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "WORKFLOW_ARCHIVED"
    publish = await client.post(f"{BASE}/{created['id']}/publish", json={"revision": 1})
    assert publish.status_code == 409
    assert len(await _audits(db, "workflow.archive")) == 1


@pytest.mark.asyncio
async def test_fingerprints_follow_run_settings_not_display(db: AsyncSession) -> None:
    _, _, agent = await seed_agent(db)
    skill = Skill(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        name="s",
        slug=f"s-{uuid.uuid4().hex[:6]}",
        content_hash="a" * 64,
    )
    db.add(skill)
    await db.flush()

    async def fingerprint() -> str:
        await db.flush()
        return (await agent_fingerprints(db, [agent.id]))[agent.id]

    base = await fingerprint()
    agent.name, agent.description, agent.is_favorite = "새 이름", "설명", True
    assert await fingerprint() == base
    agent.system_prompt = "다르게 답하세요."
    prompt = await fingerprint()
    assert prompt != base
    db.add(AgentSkillLink(agent_id=agent.id, skill_id=skill.id, config={}))
    linked = await fingerprint()
    assert linked != prompt
    skill.content_hash = "b" * 64
    edited = await fingerprint()
    assert edited != linked
    db.add(AgentGuardrailBinding(agent_id=agent.id, tenant_id=TEST_TENANT_ID, mode="off"))
    assert await fingerprint() != edited
    assert await agent_fingerprints(db, [uuid.uuid4()]) == {}


def test_every_agent_settings_table_is_classified_for_pins() -> None:
    cascading = {
        table.name
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == "agents" and fk.ondelete == "CASCADE"
    }
    assert cascading - set(PINNED_TABLES) - UNPINNED_TABLES == set()
    assert set(PINNED_TABLES) & UNPINNED_TABLES == set()
    assert {Agent.__tablename__, WorkflowVersion.__tablename__}.isdisjoint(cascading)


@pytest.mark.asyncio
async def test_using_agent_lists_draft_and_published_use_and_drift(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    other = await make_agent(db, model_id=agent.model_id, name="안 쓰는 에이전트")
    await db.commit()
    created = await _create(client)
    saved = (await _save(client, created, inquiry_workflow(str(agent.id)))).json()
    url = f"{BASE}/using-agent/{agent.id}"
    (draft_only,) = (await client.get(url)).json()
    assert (draft_only["in_draft"], draft_only["in_published"], draft_only["drifted"]) == (
        True,
        False,
        False,
    )
    await client.post(f"{BASE}/{created['id']}/publish", json={"revision": saved["draft_revision"]})
    (published,) = (await client.get(url)).json()
    assert (published["in_published"], published["drifted"]) == (True, False)
    agent.system_prompt = "바뀐 지시"
    await db.commit()
    (drifted,) = (await client.get(url)).json()
    assert drifted["drifted"] is True
    assert (await client.get(f"{BASE}/using-agent/{other.id}")).json() == []
