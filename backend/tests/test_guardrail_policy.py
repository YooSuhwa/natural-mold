"""Company guardrail policy, templates, agent bindings and run pinning (plan §3.4, §5 3, 6).

API cases act as a company member (``e09_support``); resolution and pinning use the
services directly against the test database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.guardrails import policy
from app.guardrails.pinning import RUN_METADATA_KEY, pin_run_guardrails
from app.guardrails.tool_risk import DEFAULT_ACTIONS
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.guardrail_template import AgentGuardrailBinding
from app.models.organization import Tenant
from tests.authz.d04_support import seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession, seed_agent

POLICY = f"{TENANT}/guardrails"
TEMPLATES = f"{POLICY}/templates"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def _agent(db: AsyncSession) -> uuid.UUID:
    _, _, agent = await seed_agent(db)
    await db.commit()
    return agent.id


async def _set_policy(**values: Any) -> None:
    async with TestSession() as db:
        tenant = await db.get(Tenant, TEST_TENANT_ID)
        assert tenant is not None
        tenant.settings = {**(tenant.settings or {}), "guardrails": values}
        await db.commit()


async def _bind(agent_id: uuid.UUID, mode: str, ref: str | None = None, **overrides: Any) -> None:
    async with TestSession() as db:
        db.add(
            AgentGuardrailBinding(
                agent_id=agent_id,
                tenant_id=TEST_TENANT_ID,
                mode=mode,
                template_ref=ref,
                rule_overrides=overrides,
            )
        )
        await db.commit()


async def _resolve(agent_id: uuid.UUID) -> Any:
    async with TestSession() as db:
        return await policy.resolve(db, TEST_TENANT_ID, agent_id, "standard")


def _ids(rules: Any) -> set[str]:
    return {rule.id for rule in rules.rules} if rules else set()


# --- resolution ---------------------------------------------------------------------------


async def test_without_a_company_policy_the_installation_default_applies(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _agent(db)
    assert await _resolve(agent) is None
    monkeypatch.setattr(settings, "guardrail_default_template", "kr-default")
    assert (await _resolve(agent)).template == "builtin:kr-default"


async def test_off_wins_over_the_installation_default(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _agent(db)
    monkeypatch.setattr(settings, "guardrail_default_template", "kr-default")
    await _set_policy(mode="off")

    assert await _resolve(agent) is None


async def test_open_guards_only_agents_that_chose(db: AsyncSession) -> None:
    agent = await _agent(db)
    await _set_policy(mode="open", default_template="builtin:kr-default")
    assert await _resolve(agent) is None

    await _bind(agent, "template", "builtin:kr-observe")
    assert (await _resolve(agent)).template == "builtin:kr-observe"


async def test_default_keeps_locked_rules_when_an_agent_switches_off(db: AsyncSession) -> None:
    agent = await _agent(db)
    await _set_policy(
        mode="default", default_template="builtin:kr-default", locked_rules=["secrets"]
    )
    assert _ids(await _resolve(agent)) >= {"secrets", "kr-identifiers", "people"}

    await _bind(agent, "off")
    assert _ids(await _resolve(agent)) == {"secrets"}


async def test_an_agent_template_still_carries_the_locked_rules(db: AsyncSession) -> None:
    agent = await _agent(db)
    await _set_policy(
        mode="default", default_template="builtin:kr-default", locked_rules=["secrets"]
    )
    await _bind(agent, "template", "builtin:kr-observe")

    rules = await _resolve(agent)
    assert rules.template == "builtin:kr-observe"
    assert _ids(rules) == {"observe", "secrets"}


async def test_enforced_ignores_an_agent_switching_off(db: AsyncSession) -> None:
    agent = await _agent(db)
    await _set_policy(mode="enforced", default_template="builtin:kr-default")
    await _bind(agent, "off")

    assert (await _resolve(agent)).template == "builtin:kr-default"


async def test_overrides_change_open_rules_and_leave_locked_ones(db: AsyncSession) -> None:
    agent = await _agent(db)
    await _set_policy(
        mode="default", default_template="builtin:kr-default", locked_rules=["secrets"]
    )
    await _bind(
        agent,
        "inherit",
        secrets={"enabled": False},
        contacts={"enabled": True, "action": "redact"},
        people={"enabled": False},
    )

    rules = await _resolve(agent)
    by_id = {rule.id: rule for rule in rules.rules}
    assert "secrets" in by_id and by_id["secrets"].action == "block"
    assert by_id["contacts"].action == "redact"
    assert "people" not in by_id


async def test_an_unusable_default_fails_instead_of_running_unchecked(db: AsyncSession) -> None:
    agent = await _agent(db)
    await _set_policy(mode="default", default_template=f"company:{uuid.uuid4()}")

    with pytest.raises(policy.GuardrailPolicyError):
        await _resolve(agent)


async def test_hidden_agents_are_not_guarded(db: AsyncSession) -> None:
    agent = await _agent(db)
    await _set_policy(mode="enforced", default_template="builtin:kr-default")
    async with TestSession() as check:
        assert await policy.resolve(check, TEST_TENANT_ID, agent, "builder") is None


# --- company API ------------------------------------------------------------------------


async def test_members_read_and_only_admins_change_the_policy(
    client: AsyncClient, db: AsyncSession
) -> None:
    read = await client.get(POLICY)
    assert read.status_code == 200, read.text
    body = read.json()
    assert (body["mode"], body["saved"]) == ("open", False)
    assert {t["ref"] for t in body["templates"]} >= {"builtin:kr-default", "builtin:kr-observe"}

    payload = {"mode": "default", "default_template": "builtin:kr-default"}
    denied = await client.put(POLICY, json=payload, headers={"If-Match": read.headers["ETag"]})
    assert denied.status_code == 403


async def test_admin_saves_the_policy_with_version_and_audit(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    etag = (await client.get(POLICY)).headers["ETag"]
    payload = {
        "mode": "enforced",
        "default_template": "builtin:kr-default",
        "locked_rules": ["secrets"],
    }
    stale = await client.put(POLICY, json=payload, headers={"If-Match": '"x"'})
    assert stale.status_code == 409

    saved = await client.put(POLICY, json=payload, headers={"If-Match": etag})

    assert saved.status_code == 200, saved.text
    assert (saved.json()["mode"], saved.json()["saved"]) == ("enforced", True)
    [event] = [e for e in await actions(TEST_TENANT_ID) if e.action == "guardrail.policy_update"]
    assert event.event_metadata == {
        "before": None,
        "after": {
            "mode": "enforced",
            "default_template": "builtin:kr-default",
            "locked_rules": ["secrets"],
            "tool_risk": dict(DEFAULT_ACTIONS),
        },
    }


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"mode": "default"}, "GUARDRAIL_DEFAULT_TEMPLATE_REQUIRED"),
        ({"mode": "open", "default_template": "builtin:nope"}, "GUARDRAIL_TEMPLATE_UNUSABLE"),
        (
            {"mode": "default", "default_template": "builtin:kr-default", "locked_rules": ["x"]},
            "GUARDRAIL_LOCKED_RULE_UNKNOWN",
        ),
    ],
)
async def test_policy_validation(
    client: AsyncClient, db: AsyncSession, payload: dict[str, Any], code: str
) -> None:
    await make_admin(db)
    etag = (await client.get(POLICY)).headers["ETag"]

    response = await client.put(POLICY, json=payload, headers={"If-Match": etag})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


async def test_a_template_is_drafted_published_as_versions_and_archived(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    created = await client.post(
        TEMPLATES, json={"name": "고객 상담", "copy_from": "builtin:kr-default"}
    )
    assert created.status_code == 201, created.text
    template = created.json()
    assert (template["status"], template["published_version"]) == ("draft", None)
    rules = template["draft_rules"]

    bad = await client.patch(f"{TEMPLATES}/{template['id']}", json={"rules": [{"id": "x"}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "GUARDRAIL_RULES_INVALID"

    first = await client.post(f"{TEMPLATES}/{template['id']}/publish")
    assert first.json()["published_version"] == 1
    rules[0]["action"] = "redact"
    await client.patch(f"{TEMPLATES}/{template['id']}", json={"rules": rules})
    second = await client.post(f"{TEMPLATES}/{template['id']}/publish")
    assert second.json()["published_version"] == 2

    async with TestSession() as check:
        current = await policy.template_rules(check, TEST_TENANT_ID, template["ref"])
    assert current is not None and current.version == 2
    assert current.rules[0].action == "redact"

    etag = (await client.get(POLICY)).headers["ETag"]
    await client.put(
        POLICY,
        json={"mode": "default", "default_template": template["ref"]},
        headers={"If-Match": etag},
    )
    in_use = await client.post(f"{TEMPLATES}/{template['id']}/archive")
    assert in_use.status_code == 409
    names = [e.action for e in await actions(template["id"])]
    assert names == [
        "guardrail.template_create",
        "guardrail.template_publish",
        "guardrail.template_update",
        "guardrail.template_publish",
    ]


async def test_members_cannot_create_templates(client: AsyncClient) -> None:
    response = await client.post(TEMPLATES, json={"name": "x", "copy_from": "builtin:kr-default"})
    assert response.status_code == 403


# --- agent API ----------------------------------------------------------------------------


async def test_an_agent_chooses_a_template_and_sees_what_its_next_run_gets(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await _agent(db)
    url = f"/api/agents/{agent}/guardrails"

    initial = await client.get(url)
    assert initial.status_code == 200, initial.text
    assert (initial.json()["mode"], initial.json()["effective"]) == ("inherit", None)
    assert initial.json()["policy_default_template"] is None

    saved = await client.put(
        url,
        json={
            "mode": "template",
            "template_ref": "builtin:kr-default",
            "rule_overrides": {"people": {"enabled": False}},
        },
    )

    assert saved.status_code == 200, saved.text
    effective = saved.json()["effective"]
    assert (effective["ref"], effective["name"]) == ("builtin:kr-default", "kr-default")
    assert "people" not in {rule["id"] for rule in effective["rules"]}
    [event] = [e for e in await actions(agent) if e.action == "guardrail.binding_update"]
    assert event.event_metadata is not None
    assert event.event_metadata["after"]["template_ref"] == "builtin:kr-default"


async def test_an_enforced_policy_and_locked_rules_cannot_be_undone_by_an_agent(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await _agent(db)
    url = f"/api/agents/{agent}/guardrails"
    await _set_policy(
        mode="enforced", default_template="builtin:kr-default", locked_rules=["secrets"]
    )

    view = await client.get(url)
    assert view.json()["policy_default_template"] == "builtin:kr-default"
    off = await client.put(url, json={"mode": "off"})
    locked = await client.put(
        url, json={"mode": "inherit", "rule_overrides": {"secrets": {"enabled": False}}}
    )

    assert off.status_code == 409 and off.json()["error"]["code"] == "GUARDRAIL_POLICY_ENFORCED"
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "GUARDRAIL_RULE_LOCKED"


async def test_another_users_agent_is_not_found(client: AsyncClient, db: AsyncSession) -> None:
    other = uuid.uuid4()
    _, _, agent = await seed_agent(db, user_id=other)
    await db.commit()

    response = await client.get(f"/api/agents/{agent.id}/guardrails")

    assert response.status_code == 404


# --- pinning ------------------------------------------------------------------------------


async def _run(db: AsyncSession, agent_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    conversation = Conversation(
        agent_id=agent_id,
        title="pin",
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot="Test Agent",
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.flush()
    return conversation.id, await _new_run(db, conversation.id, agent_id)


async def _new_run(db: AsyncSession, conversation_id: uuid.UUID, agent_id: uuid.UUID) -> uuid.UUID:
    run = ConversationRun(
        conversation_id=conversation_id,
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="running",
        is_active=False,
    )
    db.add(run)
    await db.commit()
    return run.id


async def _pin(
    run_id: uuid.UUID, conversation_id: uuid.UUID, agent: uuid.UUID, resume: bool
) -> Any:
    async with TestSession() as db:
        pinned = await pin_run_guardrails(
            db,
            run_id=run_id,
            conversation_id=conversation_id,
            context=unit_context(TEST_USER_ID),
            agent_id=agent,
            runtime_profile="standard",
            resume=resume,
        )
        return pinned.rules


async def test_a_running_turn_keeps_its_rules_and_the_next_turn_gets_the_new_policy(
    db: AsyncSession,
) -> None:
    agent = await _agent(db)
    await _set_policy(mode="default", default_template="builtin:kr-default")
    conversation_id, first = await _run(db, agent)

    pinned = await _pin(first, conversation_id, agent, resume=False)
    assert pinned.template == "builtin:kr-default"
    async with TestSession() as check:
        row = await check.get(ConversationRun, first)
        assert row is not None and row.metadata_json is not None
        assert row.metadata_json[RUN_METADATA_KEY]["rules"]["template"] == "builtin:kr-default"

    await _set_policy(mode="default", default_template="builtin:kr-observe")
    resumed = await _pin(await _new_run(db, conversation_id, agent), conversation_id, agent, True)
    next_turn = await _pin(
        await _new_run(db, conversation_id, agent), conversation_id, agent, False
    )

    assert resumed.template == "builtin:kr-default"  # an approval answered: the old rules
    assert next_turn.template == "builtin:kr-observe"


async def test_off_is_pinned_too(db: AsyncSession) -> None:
    agent = await _agent(db)
    conversation_id, first = await _run(db, agent)

    assert await _pin(first, conversation_id, agent, resume=False) is None
    await _set_policy(mode="enforced", default_template="builtin:kr-default")
    resumed = await _pin(await _new_run(db, conversation_id, agent), conversation_id, agent, True)

    assert resumed is None


async def test_tool_risk_is_pinned_and_a_resume_keeps_it(db: AsyncSession) -> None:
    agent = await _agent(db)
    await _set_policy(mode="off", tool_risk={"external_mutation": "block"})
    conversation_id, first = await _run(db, agent)

    async with TestSession() as session:
        pinned = await pin_run_guardrails(
            session,
            run_id=first,
            conversation_id=conversation_id,
            context=unit_context(TEST_USER_ID),
            agent_id=agent,
            runtime_profile="standard",
            resume=False,
        )
    assert pinned.tool_risk is not None
    assert pinned.tool_risk.action("external_mutation") == "block"

    await _set_policy(mode="off")
    async with TestSession() as session:
        resumed = await pin_run_guardrails(
            session,
            run_id=await _new_run(db, conversation_id, agent),
            conversation_id=conversation_id,
            context=unit_context(TEST_USER_ID),
            agent_id=agent,
            runtime_profile="standard",
            resume=True,
        )
        fresh = await pin_run_guardrails(
            session,
            run_id=await _new_run(db, conversation_id, agent),
            conversation_id=conversation_id,
            context=unit_context(TEST_USER_ID),
            agent_id=agent,
            runtime_profile="standard",
            resume=False,
        )
    assert (
        resumed.tool_risk is not None and resumed.tool_risk.action("external_mutation") == "block"
    )
    assert fresh.tool_risk is None
