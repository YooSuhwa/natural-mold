from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.models.credential import Credential
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


def _skill_md(name: str, description: str, body: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\nversion: "1.0.0"\n---\n\n{body}\n'


@pytest.mark.asyncio
async def test_login_success_writes_audit(raw_client, db: AsyncSession) -> None:
    await raw_client.post(
        "/api/auth/register",
        json={
            "email": "audit-login@test.com",
            "password": "correct horse battery staple 42",
            "display_name": "Audit User",
        },
    )

    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.action == "auth.login")))
        .scalars()
        .all()
    )
    assert rows
    assert rows[-1].outcome == "success"
    assert rows[-1].actor_email_snapshot == "audit-login@test.com"


@pytest.mark.asyncio
async def test_login_failure_writes_audit(raw_client, db: AsyncSession) -> None:
    response = await raw_client.post(
        "/api/auth/login",
        json={"email": "missing@test.com", "password": "wrong-password"},
    )
    assert response.status_code == 401

    row = (
        await db.execute(select(AuditEvent).where(AuditEvent.action == "auth.login"))
    ).scalar_one()
    assert row.outcome == "failure"
    assert row.reason_code == "invalid_credentials"
    assert row.actor_email_snapshot == "missing@test.com"


@pytest.mark.asyncio
async def test_credential_create_writes_global_audit(client, db: AsyncSession) -> None:
    response = await client.post(
        "/api/credentials",
        json={
            "definition_key": "openai",
            "name": "Audit OpenAI",
            "data": {"api_key": "sk-secret"},
        },
    )
    assert response.status_code == 201
    cred_id = response.json()["id"]

    row = (
        await db.execute(
            select(AuditEvent).where(
                AuditEvent.action == "credential.create",
                AuditEvent.target_id == cred_id,
            )
        )
    ).scalar_one()
    assert row.outcome == "success"
    assert row.event_metadata is not None
    assert row.event_metadata["definition_key"] == "openai"
    assert "sk-secret" not in str(row.event_metadata)


@pytest.mark.asyncio
async def test_credential_delete_preserves_global_audit(client, db: AsyncSession) -> None:
    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "openai",
            "name": "Delete Audit",
            "data": {"api_key": "sk-secret"},
        },
    )
    cred_id = create.json()["id"]

    response = await client.delete(f"/api/credentials/{cred_id}")
    assert response.status_code == 204

    cred = (
        await db.execute(select(Credential).where(Credential.id == uuid.UUID(cred_id)))
    ).scalar_one_or_none()
    assert cred is None

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "credential",
                    AuditEvent.target_id == cred_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in rows} >= {"credential.create", "credential.delete"}


async def _create_model() -> str:
    async with TestSession() as db:
        model = Model(
            provider="openai",
            model_name="gpt-4o",
            display_name="GPT-4o",
            is_default=True,
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return str(model.id)


@pytest.mark.asyncio
async def test_agent_mutations_write_audit_without_prompt(
    client,
    db: AsyncSession,
) -> None:
    model_id = await _create_model()
    create = await client.post(
        "/api/agents",
        json={
            "name": "Audit Agent",
            "description": "A test agent",
            "system_prompt": "secret instruction text",
            "model_id": model_id,
        },
    )
    assert create.status_code == 201, create.text
    agent_id = create.json()["id"]

    update = await client.put(
        f"/api/agents/{agent_id}",
        json={"name": "Audit Agent Updated", "system_prompt": "new secret prompt"},
    )
    assert update.status_code == 200, update.text

    delete = await client.delete(f"/api/agents/{agent_id}")
    assert delete.status_code == 204

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "agent",
                    AuditEvent.target_id == agent_id,
                )
            )
        )
        .scalars()
        .all()
    )
    actions = {row.action for row in rows}
    assert actions >= {
        "agent.create",
        "agent.update",
        "agent.archive",
    }
    assert "agent.delete" not in actions
    assert "secret instruction text" not in str([row.event_metadata for row in rows])
    assert "new secret prompt" not in str([row.event_metadata for row in rows])


@pytest.mark.asyncio
async def test_tool_mutations_write_audit(client, db: AsyncSession) -> None:
    create = await client.post(
        "/api/tools",
        json={
            "definition_key": "http_request",
            "name": "Audit Ping",
            "parameters": {"method": "GET", "url": "https://example.com"},
        },
    )
    assert create.status_code == 201, create.text
    tool_id = create.json()["id"]

    update = await client.patch(
        f"/api/tools/{tool_id}",
        json={"enabled": False},
    )
    assert update.status_code == 200, update.text

    delete = await client.delete(f"/api/tools/{tool_id}")
    assert delete.status_code == 204

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "tool",
                    AuditEvent.target_id == tool_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in rows} >= {"tool.create", "tool.update", "tool.delete"}


@pytest.mark.asyncio
async def test_mcp_server_mutations_write_audit_without_headers(
    client,
    db: AsyncSession,
) -> None:
    create = await client.post(
        "/api/mcp-servers",
        json={
            "name": "Audit MCP",
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "headers": {"Authorization": "Bearer secret-token"},
        },
    )
    assert create.status_code == 201, create.text
    server_id = create.json()["id"]

    update = await client.patch(
        f"/api/mcp-servers/{server_id}",
        json={
            "name": "Audit MCP Updated",
            "headers": {"Authorization": "Bearer new-secret-token"},
        },
    )
    assert update.status_code == 200, update.text

    delete = await client.delete(f"/api/mcp-servers/{server_id}")
    assert delete.status_code == 204

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "mcp_server",
                    AuditEvent.target_id == server_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in rows} >= {
        "mcp_server.create",
        "mcp_server.update",
        "mcp_server.delete",
    }
    metadata_text = str([row.event_metadata for row in rows])
    assert "secret-token" not in metadata_text
    assert "new-secret-token" not in metadata_text


@pytest.mark.asyncio
async def test_mcp_probe_writes_audit_with_outcome(client, db: AsyncSession, monkeypatch) -> None:
    """probe 감사의 action/outcome/reason_code/metadata 계약 잠금.

    Stage 2 적대 리뷰 mutation 실증: action 이름을 변조해도 기존 테스트가
    전부 그린이었다 — 감사는 규정준수 표면이라 outcome 반전이 조용히
    통과하면 안 된다. 라우터는 connect_and_list를 이름으로 import하므로
    app.routers.mcp 경로를 패치한다.
    """

    async def _stub_ok(**_) -> dict:
        return {"success": True, "server_info": {"name": "x"}, "tools": [{"name": "echo"}]}

    monkeypatch.setattr("app.routers.mcp.connect_and_list", _stub_ok)
    ok = await client.post(
        "/api/mcp-servers/probe",
        json={"transport": "streamable_http", "url": "https://mcp.example.com"},
    )
    assert ok.status_code == 200, ok.text

    async def _stub_fail(**_) -> dict:
        return {"success": False, "server_info": {}, "tools": [], "error": "boom"}

    monkeypatch.setattr("app.routers.mcp.connect_and_list", _stub_fail)
    fail = await client.post(
        "/api/mcp-servers/probe",
        json={"transport": "streamable_http", "url": "https://mcp.example.com"},
    )
    assert fail.status_code == 200, fail.text

    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.action == "mcp_server.probe")))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    # created_at 정렬 대신 outcome으로 행을 식별 — 같은 테스트 안의 연속
    # 요청은 타임스탬프 tie로 정렬이 비결정일 수 있다.
    by_outcome = {row.outcome: row for row in rows}
    ok_row, fail_row = by_outcome["success"], by_outcome["failure"]
    assert ok_row.target_type == "mcp_server_probe"
    assert ok_row.outcome == "success"
    assert ok_row.reason_code is None
    assert ok_row.event_metadata is not None
    assert ok_row.event_metadata["transport"] == "streamable_http"
    assert ok_row.event_metadata["tool_count"] == 1
    assert fail_row.outcome == "failure"
    assert fail_row.reason_code == "mcp_probe_failed"
    assert fail_row.reason_message == "boom"
    assert fail_row.event_metadata is not None
    assert fail_row.event_metadata["tool_count"] == 0


@pytest.mark.asyncio
async def test_mcp_tool_review_writes_audit_with_decision_and_states(
    client, db: AsyncSession, monkeypatch
) -> None:
    """D11 tool review: action, outcome, the decided version and the state change."""

    async def _probe(**_) -> dict:
        tool = {"name": "send", "description": "send", "input_schema": {"type": "object"}}
        return {"success": True, "server_info": {}, "tools": [tool], "error": None}

    monkeypatch.setattr("app.mcp.discovery.connect_and_list", _probe)
    created = await client.post(
        "/api/mcp-servers",
        json={
            "name": "Review MCP",
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
        },
    )
    assert created.status_code == 201, created.text
    server_id = created.json()["id"]
    [tool] = (await client.post(f"/api/mcp-servers/{server_id}/discover")).json()["tools"]
    fingerprint = tool["pending_version"]["fingerprint"]
    review_url = f"/api/mcp-servers/{server_id}/tools/{tool['id']}/review"

    stale = await client.post(review_url, json={"decision": "approve", "fingerprint": "0" * 64})
    assert stale.status_code == 409
    approved = await client.post(
        review_url, json={"decision": "approve", "fingerprint": fingerprint}
    )
    assert approved.status_code == 200, approved.text

    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.action == "mcp_tool.review")))
        .scalars()
        .all()
    )
    by_outcome = {row.outcome: row for row in rows}
    assert set(by_outcome) == {"success", "denied"}
    ok, denied = by_outcome["success"], by_outcome["denied"]
    assert (ok.target_type, str(ok.target_id)) == ("mcp_server", server_id)
    assert ok.reason_code is None
    assert ok.event_metadata is not None
    assert ok.event_metadata["tool_name"] == "send"
    assert ok.event_metadata["decision"] == "approve"
    assert ok.event_metadata["fingerprint"] == fingerprint
    assert (ok.event_metadata["state_before"], ok.event_metadata["state_after"]) == (
        "pending",
        "approved",
    )
    assert denied.reason_code == "mcp_tool_review_conflict"
    assert denied.event_metadata is not None and denied.event_metadata["decision"] == "approve"


@pytest.mark.asyncio
async def test_mcp_import_writes_audit_with_error_metadata(client, db: AsyncSession) -> None:
    """import 감사의 부분 실패(success + reason_code)/전량 실패(failure) 판정 잠금."""

    partial = await client.post(
        "/api/mcp-servers/import",
        json={
            "mcpServers": {
                "good": {"transport": "streamable_http", "url": "https://a.example"},
                "bad": {"transport": "streamable_http"},
            }
        },
    )
    assert partial.status_code == 200, partial.text
    body = partial.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 1

    all_fail = await client.post(
        "/api/mcp-servers/import",
        json={"mcpServers": {"bad2": {"transport": "streamable_http"}}},
    )
    assert all_fail.status_code == 200, all_fail.text

    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.action == "mcp_server.import")))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    by_outcome = {row.outcome: row for row in rows}
    partial_row, all_fail_row = by_outcome["success"], by_outcome["failure"]
    # 일부라도 created/updated가 있으면 success + mcp_import_errors reason
    assert partial_row.outcome == "success"
    assert partial_row.reason_code == "mcp_import_errors"
    assert partial_row.event_metadata is not None
    assert partial_row.event_metadata["created"] == 1
    assert partial_row.event_metadata["error_count"] == 1
    assert partial_row.event_metadata["entry_count"] == 2
    assert partial_row.event_metadata["overwrite"] is False
    # 전량 실패면 failure
    assert all_fail_row.outcome == "failure"
    assert all_fail_row.reason_code == "mcp_import_errors"


@pytest.mark.asyncio
async def test_skill_mutations_write_audit_without_content(
    client,
    db: AsyncSession,
) -> None:
    create = await client.post(
        "/api/skills",
        json={
            "name": "Audit Skill",
            "slug": "audit-skill",
            "description": "audit test",
            "content": _skill_md("audit-skill", "audit test", "secret skill body"),
        },
    )
    assert create.status_code == 201, create.text
    skill_id = create.json()["id"]

    update = await client.patch(
        f"/api/skills/{skill_id}",
        json={"name": "Audit Skill Updated", "description": "updated"},
    )
    assert update.status_code == 200, update.text

    content = await client.put(
        f"/api/skills/{skill_id}/content",
        json={
            "content": _skill_md(
                "audit-skill",
                "audit test updated",
                "new secret skill body",
            )
        },
    )
    assert content.status_code == 200, content.text

    delete = await client.delete(f"/api/skills/{skill_id}")
    assert delete.status_code == 204

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "skill",
                    AuditEvent.target_id == skill_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in rows} >= {
        "skill.create",
        "skill.update",
        "skill.content_update",
        "skill.delete",
    }
    metadata_text = str([row.event_metadata for row in rows])
    assert "secret skill body" not in metadata_text
    assert "new secret skill body" not in metadata_text


@pytest.mark.asyncio
async def test_trigger_mutations_write_audit_without_input_message(
    client,
    db: AsyncSession,
) -> None:
    model_id = await _create_model()
    agent = await client.post(
        "/api/agents",
        json={
            "name": "Audit Trigger Agent",
            "system_prompt": "You are helpful.",
            "model_id": model_id,
            "identity_mode": "fixed",
        },
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]

    create = await client.post(
        f"/api/agents/{agent_id}/triggers",
        json={
            "name": "Audit Trigger",
            "trigger_type": "interval",
            "schedule_config": {"interval_minutes": 30},
            "input_message": "secret scheduled instruction",
        },
    )
    assert create.status_code == 201, create.text
    trigger_id = create.json()["id"]

    update = await client.put(
        f"/api/agents/{agent_id}/triggers/{trigger_id}",
        json={"status": "paused", "input_message": "new secret scheduled instruction"},
    )
    assert update.status_code == 200, update.text

    delete = await client.delete(f"/api/agents/{agent_id}/triggers/{trigger_id}")
    assert delete.status_code == 204

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "trigger",
                    AuditEvent.target_id == trigger_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in rows} >= {
        "trigger.create",
        "trigger.update",
        "trigger.delete",
    }
    metadata_text = str([row.event_metadata for row in rows])
    assert "secret scheduled instruction" not in metadata_text
    assert "new secret scheduled instruction" not in metadata_text


@pytest.mark.asyncio
async def test_conversation_and_share_mutations_write_audit(
    client,
    db: AsyncSession,
) -> None:
    model_id = await _create_model()
    agent = await client.post(
        "/api/agents",
        json={
            "name": "Audit Conversation Agent",
            "system_prompt": "You are helpful.",
            "model_id": model_id,
        },
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]

    create = await client.post(
        f"/api/agents/{agent_id}/conversations",
        json={"title": "Audit Conversation"},
    )
    assert create.status_code == 201, create.text
    conversation_id = create.json()["id"]

    update = await client.patch(
        f"/api/conversations/{conversation_id}",
        json={"title": "Audit Conversation Updated", "is_pinned": True},
    )
    assert update.status_code == 200, update.text

    share = await client.post(f"/api/conversations/{conversation_id}/share")
    assert share.status_code == 200, share.text
    share_token = share.json()["share_token"]

    revoke = await client.delete(f"/api/conversations/{conversation_id}/share")
    assert revoke.status_code == 204

    delete = await client.delete(f"/api/conversations/{conversation_id}")
    assert delete.status_code == 204

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "conversation",
                    AuditEvent.target_id == conversation_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in rows} >= {
        "conversation.create",
        "conversation.update",
        "conversation.share_create",
        "conversation.share_revoke",
        "conversation.delete",
    }
    assert share_token not in str([row.event_metadata for row in rows])


@pytest.mark.asyncio
async def test_agent_api_control_plane_writes_audit_without_api_key(
    client,
    db: AsyncSession,
) -> None:
    model_id = await _create_model()
    agent = await client.post(
        "/api/agents",
        json={
            "name": "Audit API Agent",
            "system_prompt": "You are helpful.",
            "model_id": model_id,
            "identity_mode": "fixed",
        },
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]

    deployment = await client.post(
        "/api/agent-api/deployments",
        json={"agent_id": agent_id, "rate_limit_per_minute": 60},
    )
    assert deployment.status_code == 201, deployment.text
    deployment_id = deployment.json()["id"]

    update = await client.patch(
        f"/api/agent-api/deployments/{deployment_id}",
        json={"status": "disabled"},
    )
    assert update.status_code == 200, update.text

    key = await client.post(
        "/api/agent-api/keys",
        json={
            "name": "Audit API Key",
            "scopes": ["invoke"],
            "allow_all_deployments": False,
            "deployment_ids": [deployment_id],
        },
    )
    assert key.status_code == 201, key.text
    api_key_id = key.json()["id"]
    cleartext = key.json()["key"]

    revoke = await client.post(f"/api/agent-api/keys/{api_key_id}/revoke")
    assert revoke.status_code == 200, revoke.text

    deployment_rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "agent_deployment",
                    AuditEvent.target_id == deployment_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in deployment_rows} >= {
        "agent_api.deployment_create",
        "agent_api.deployment_update",
    }

    key_rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "agent_api_key",
                    AuditEvent.target_id == api_key_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in key_rows} >= {
        "agent_api.key_create",
        "agent_api.key_revoke",
    }
    assert cleartext not in str([row.event_metadata for row in key_rows])


@pytest.mark.asyncio
async def test_model_catalog_mutations_write_audit(client, db: AsyncSession) -> None:
    create = await client.post(
        "/api/models",
        json={
            "provider": "openai",
            "model_name": f"gpt-audit-{uuid.uuid4().hex[:8]}",
            "display_name": "GPT Audit",
            "source": "manual",
        },
    )
    assert create.status_code == 201, create.text
    model_id = create.json()["id"]

    update = await client.patch(
        f"/api/models/{model_id}",
        json={"display_name": "GPT Audit Updated", "supports_reasoning": True},
    )
    assert update.status_code == 200, update.text

    delete = await client.delete(f"/api/models/{model_id}")
    assert delete.status_code == 204

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "model",
                    AuditEvent.target_id == model_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in rows} >= {
        "model.create",
        "model.update",
        "model.delete",
    }


@pytest.mark.asyncio
async def test_system_llm_setting_update_writes_audit(client, db: AsyncSession) -> None:
    response = await client.put(
        "/api/system-llm-settings/text_primary",
        json={"credential_id": None, "model_name": None},
    )
    assert response.status_code == 200, response.text

    row = (
        await db.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "system_llm_setting",
                AuditEvent.target_id == "text_primary",
            )
        )
    ).scalar_one()
    assert row.action == "system_llm_setting.update"
    assert row.outcome == "success"


@pytest.mark.asyncio
async def test_marketplace_management_writes_audit_without_release_notes(
    client,
    db: AsyncSession,
) -> None:
    skill = await client.post(
        "/api/skills",
        json={
            "name": "Audit Marketplace Skill",
            "slug": f"audit-marketplace-{uuid.uuid4().hex[:8]}",
            "description": "audit marketplace",
            "content": _skill_md(
                "audit-marketplace-skill",
                "audit marketplace",
                "marketplace skill body",
            ),
        },
    )
    assert skill.status_code == 201, skill.text
    skill_id = skill.json()["id"]

    publish = await client.post(
        f"/api/marketplace/items/from-skill/{skill_id}",
        json={
            "visibility": "public",
            "name": f"Audit Marketplace {uuid.uuid4().hex[:8]}",
            "description": "audit publish",
            "release_notes": "secret release notes",
        },
    )
    assert publish.status_code == 201, publish.text
    item_id = publish.json()["id"]

    patch = await client.patch(
        f"/api/marketplace/items/{item_id}",
        json={"description": "updated publish", "tags": ["audit"]},
    )
    assert patch.status_code == 200, patch.text

    disable = await client.post(f"/api/marketplace/items/{item_id}/disable")
    assert disable.status_code == 200, disable.text

    enable = await client.post(f"/api/marketplace/items/{item_id}/enable")
    assert enable.status_code == 200, enable.text

    listed = await client.post(
        f"/api/marketplace/admin/items/{item_id}/listed",
        json={"is_listed": True},
    )
    assert listed.status_code == 200, listed.text

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.target_type == "marketplace_item",
                    AuditEvent.target_id == item_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.action for row in rows} >= {
        "marketplace.publish",
        "marketplace.item_update",
        "marketplace.item_disable",
        "marketplace.item_enable",
        "marketplace.admin_set_listed",
    }
    metadata_text = str([row.event_metadata for row in rows])
    assert "secret release notes" not in metadata_text
    assert "marketplace skill body" not in metadata_text


async def test_guardrail_block_writes_audit_without_the_value(db: AsyncSession) -> None:
    from app.guardrails.events import RunScope
    from app.guardrails.middleware import Guard
    from app.guardrails.rules import BUILTIN_TEMPLATES
    from tests.authz.unit_scope_fixture import unit_context
    from tests.conftest import seed_agent

    await seed_agent(db)
    secret = "sk-abcdefghijklmnopqrstuvwx"
    guard = Guard(
        BUILTIN_TEMPLATES["kr-default"],
        None,
        RunScope(unit_context(TEST_USER_ID), None, None, "run-audit"),
    )

    await guard.check([f"키 {secret}"], "llm_send")
    await guard.flush()

    async with TestSession() as check:
        events = list(
            await check.scalars(select(AuditEvent).where(AuditEvent.action == "guardrail.block"))
        )
    assert len(events) == 1
    event = events[0]
    assert (event.actor_type, event.outcome, event.reason_code) == ("system", "denied", "secrets")
    assert event.run_id == "run-audit"
    assert event.event_metadata is not None
    assert event.event_metadata["stage"] == "llm_send"
    assert event.event_metadata["entity"] == "SECRET"
    assert secret not in str(event.event_metadata)


@pytest.mark.asyncio
async def test_guardrail_policy_mutations_write_audit(client, db: AsyncSession, test_app) -> None:
    from datetime import datetime

    from app.services.tenants.versioning import etag as version_tag
    from tests.authz.d04_support import seed_scope
    from tests.authz.d05_support import TEST_ORG_ID
    from tests.authz.e09_support import MEMBER, TENANT, acting_as, make_admin
    from tests.authz.unit_scope_fixture import TEST_TENANT_ID
    from tests.conftest import seed_agent

    await seed_scope(db)
    await make_admin(db)
    _, _, agent = await seed_agent(db)
    await db.commit()
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        etag = (await client.get(f"{TENANT}/guardrails")).headers["ETag"]
        policy = await client.put(
            f"{TENANT}/guardrails",
            json={
                "mode": "open",
                "default_template": "builtin:kr-observe",
                "tool_risk": {"external_mutation": "block"},
            },
            headers={"If-Match": etag},
        )
        created = await client.post(
            f"{TENANT}/guardrails/templates",
            json={"name": "상담", "copy_from": "builtin:kr-default"},
        )
        template_id = created.json()["id"]

        def version(body: dict) -> dict[str, str]:
            return {"If-Match": version_tag(datetime.fromisoformat(body["updated_at"]))}

        published = await client.post(
            f"{TENANT}/guardrails/templates/{template_id}/publish", headers=version(created.json())
        )
        again = await client.post(
            f"{TENANT}/guardrails/templates/{template_id}/publish",
            headers=version(published.json()),
        )
        reverted = await client.post(
            f"{TENANT}/guardrails/templates/{template_id}/revert",
            json={"version": 1},
            headers=version(again.json()),
        )
        archived = await client.post(
            f"{TENANT}/guardrails/templates/{template_id}/archive",
            headers=version(reverted.json()),
        )
        binding = await client.put(f"/api/agents/{agent.id}/guardrails", json={"mode": "off"})
    for response in (policy, created, published, again, reverted, archived, binding):
        assert response.status_code in (200, 201), response.text

    rows = list(
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action.like("guardrail.%"))
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    by_action = {row.action: row for row in rows}
    assert set(by_action) == {
        "guardrail.policy_update",
        "guardrail.template_create",
        "guardrail.template_publish",
        "guardrail.template_archive",
        "guardrail.template_revert",
        "guardrail.binding_update",
    }
    for row in rows:
        assert (row.actor_type, row.actor_user_id, row.outcome) == ("user", TEST_USER_ID, "success")
        assert row.tenant_id == TEST_TENANT_ID

    def after(action: str) -> dict:
        metadata = by_action[action].event_metadata
        assert metadata is not None
        return metadata["after"]

    assert after("guardrail.policy_update")["mode"] == "open"
    assert after("guardrail.policy_update")["tool_risk"]["external_mutation"] == "block"
    assert by_action["guardrail.template_create"].target_id == template_id
    publishes = [row for row in rows if row.action == "guardrail.template_publish"]
    assert sorted((row.event_metadata or {})["after"]["version"] for row in publishes) == [1, 2]
    assert after("guardrail.template_archive")["status"] == "archived"
    assert after("guardrail.template_revert") == {"version": 3, "from_version": 1}
    bound = by_action["guardrail.binding_update"]
    assert (bound.target_type, bound.target_id) == ("agent", str(agent.id))
    assert bound.event_metadata == {
        "before": {"mode": "inherit", "template_ref": None, "rule_overrides": {}},
        "after": {"mode": "off", "template_ref": None, "rule_overrides": {}},
    }


@pytest.mark.asyncio
async def test_mcp_tool_call_decision_writes_audit_without_argument_values(
    db: AsyncSession,
) -> None:
    """D10 decision record: actor, tool, outcome, reviewed version; argument names only."""
    from app.mcp import decisions
    from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
    from tests.conftest import seed_agent

    await seed_agent(db)
    await db.commit()
    context = decisions.DecisionContext.from_config(
        {
            "tenant_id": str(TEST_TENANT_ID),
            "org_id": str(TEST_ORG_ID),
            "user_id": str(TEST_USER_ID),
            "mcp_tool_id": "22222222-2222-2222-2222-222222222222",
            "mcp_tool_name": "send",
            "mcp_reviewed_fingerprint": "a" * 64,
        },
        risk_level="destructive",
        gateway=True,
    )
    await decisions.record(
        context,
        outcome="denied",
        reason_code="mcp_gateway_refused",
        arguments={"to": "ceo@example.com"},
        attempts=1,
        run_id="run-decision",
    )

    async with TestSession() as check:
        [event] = list(
            await check.scalars(select(AuditEvent).where(AuditEvent.action == "mcp.tool_call"))
        )
    assert (event.outcome, event.reason_code, event.run_id) == (
        "denied",
        "mcp_gateway_refused",
        "run-decision",
    )
    assert (event.target_type, event.target_name_snapshot) == ("mcp_tool", "send")
    assert event.actor_user_id == TEST_USER_ID
    assert event.event_metadata is not None
    assert event.event_metadata["reviewed_fingerprint"] == "a" * 64
    assert event.event_metadata["risk_level"] == "destructive"
    assert event.event_metadata["argument_names"] == ["to"]
    assert "ceo@example.com" not in str(event.event_metadata)


@pytest.mark.asyncio
async def test_approval_request_audit_records_the_request_the_decision_and_the_expiry(
    db: AsyncSession,
) -> None:
    """17.2 approval record: requester, decider, decision number, args hash; never args."""
    from datetime import timedelta

    from app.approvals import service
    from tests.authz.g03_support import approval_payload, seed_conversation, seed_interrupted

    conversation = await seed_conversation(db)
    payload = approval_payload()
    payload["action_requests"][1]["args"] = {"to": "ceo@example.com"}
    runs = []
    for _ in range(2):
        run = await db.get(ConversationRun, await seed_interrupted(db, conversation, payload))
        assert run is not None
        run.tenant_id, run.org_id, run.run_source = (
            conversation.tenant_id,
            conversation.org_id,
            "TRIGGER",
        )
        runs.append(run)
    await db.commit()
    rows = []
    for run in runs:
        rows += await service.record_pending(
            db, run, [service.PendingInterrupt("intr-1", payload)], origin=service.RunOrigin("A")
        )
    rows[1].expires_at = service.now() - timedelta(seconds=1)
    await db.commit()
    await service.claim(
        db,
        service.Claim(runs[0].id, {"intr-1": ["approve", "reject"]}, "inbox", TEST_USER_ID),
    )
    await service.claim(
        db, service.Claim(runs[1].id, {"intr-1": ["reject", "reject"]}, "expiry", None)
    )
    await db.commit()

    async with TestSession() as check:
        events = list(
            await check.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.like("approval.%"))
                .order_by(AuditEvent.created_at)
            )
        )
    by_action: dict[str, list[AuditEvent]] = {}
    for event in events:
        by_action.setdefault(event.action, []).append(event)
    assert sorted(by_action) == ["approval.decided", "approval.expired", "approval.requested"]
    assert len(by_action["approval.requested"]) == 2
    requested = by_action["approval.requested"][0]
    assert (requested.actor_type, requested.outcome, requested.target_type) == (
        "system",
        "success",
        "approval_request",
    )
    assert requested.target_name_snapshot == "execute_in_skill, send_email"
    assert requested.target_owner_user_id == TEST_USER_ID
    [decided] = by_action["approval.decided"]
    assert (decided.actor_type, decided.actor_user_id, decided.outcome) == (
        "user",
        TEST_USER_ID,
        "success",
    )
    assert decided.target_id == str(rows[0].id) and decided.run_id == str(runs[0].id)
    metadata = decided.event_metadata or {}
    assert metadata["channel"] == "inbox" and metadata["decision_no"] == 1
    assert metadata["decisions"] == ["approve", "reject"] and metadata["source"] == "trigger"
    assert metadata["args_digest"] == service.args_digest(payload)
    assert metadata["decider_user_id"] == str(TEST_USER_ID)
    [expired] = by_action["approval.expired"]
    assert (expired.actor_type, expired.outcome) == ("system", "denied")
    assert (expired.event_metadata or {})["status"] == "expired"
    assert "ceo@example.com" not in str([e.event_metadata for e in events])


@pytest.mark.asyncio
async def test_emergency_stop_audit_records_who_what_and_how_much_was_stopped(
    client, db: AsyncSession
) -> None:
    """17.4: the stop and its release, with the counts of what they touched."""
    from tests.test_emergency_stop import _agent_with_work

    _conversation, work = await _agent_with_work(db)
    stopped = await client.post(
        "/api/emergency-stops",
        json={"scope_type": "agent", "scope_id": str(work["agent_id"]), "reason": "오발송"},
    )
    await client.post(
        f"/api/emergency-stops/{stopped.json()['id']}/release", json={"reason": "확인"}
    )

    async with TestSession() as check:
        rows = list(
            await check.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.like("emergency_stop.%"))
                .order_by(AuditEvent.created_at)
            )
        )
    by_action = {row.action: row for row in rows}
    assert sorted(by_action) == ["emergency_stop.apply", "emergency_stop.release"]
    applied, released = by_action["emergency_stop.apply"], by_action["emergency_stop.release"]
    for row in rows:
        assert (row.actor_type, row.actor_user_id, row.outcome) == ("user", TEST_USER_ID, "success")
        assert (row.target_type, row.target_id) == ("agent", str(work["agent_id"]))
        assert row.target_owner_user_id == TEST_USER_ID
    assert applied.reason_message == "오발송" and released.reason_message == "확인"
    assert applied.event_metadata == {
        "stop_id": stopped.json()["id"],
        "runs_cancel_requested": 1,
        "schedules_paused": 1,
        "deployments_disabled": 1,
        "approvals_canceled": 1,
        "agents": 1,
    }


@pytest.mark.asyncio
async def test_personal_memory_mutations_write_audit_without_text(
    client,
    db: AsyncSession,
) -> None:
    secret = "나의 비밀 메모 내용 7731"
    create = await client.post("/api/memories", json={"scope": "user", "content": secret})
    assert create.status_code == 201, create.text
    memory_id = create.json()["id"]
    update = await client.patch(f"/api/memories/{memory_id}", json={"content": secret + " 수정"})
    assert update.status_code == 200, update.text
    delete = await client.delete(f"/api/memories/{memory_id}")
    assert delete.status_code == 204

    approve = await client.post("/api/memory-proposals", json={"scope": "user", "content": secret})
    assert approve.status_code == 201, approve.text
    approved_id = approve.json()["id"]
    decided = await client.post(f"/api/memory-proposals/{approved_id}/approve")
    assert decided.status_code == 200, decided.text
    record_id = decided.json()["memory"]["id"]
    reject = await client.post("/api/memory-proposals", json={"scope": "user", "content": secret})
    rejected_id = reject.json()["id"]
    assert (await client.post(f"/api/memory-proposals/{rejected_id}/reject")).status_code == 200

    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.action.like("memory.%"))))
        .scalars()
        .all()
    )
    by_target = {(row.action, row.target_id): row for row in rows}

    def meta(action: str, target: str) -> dict:
        metadata = by_target[(action, target)].event_metadata
        assert metadata is not None
        return metadata

    expected = {
        ("memory.record_create", memory_id): "memory_record",
        ("memory.record_update", memory_id): "memory_record",
        ("memory.record_delete", memory_id): "memory_record",
        ("memory.proposal_create", approved_id): "memory_proposal",
        ("memory.proposal_approve", approved_id): "memory_proposal",
        ("memory.proposal_create", rejected_id): "memory_proposal",
        ("memory.proposal_reject", rejected_id): "memory_proposal",
    }
    for (action, target), target_type in expected.items():
        row = by_target[(action, target)]
        assert row.target_type == target_type
        assert row.outcome == "success"
        assert str(row.actor_user_id) == str(TEST_USER_ID)
        assert meta(action, target)["scope"] == "user"
    created = meta("memory.record_create", memory_id)
    assert created["channel"] == "direct"
    assert created["length"] == len(secret)
    assert meta("memory.record_update", memory_id)["fields"] == ["content"]
    approved = meta("memory.proposal_approve", approved_id)
    assert approved["record_id"] == record_id
    assert approved["edited"] is False
    assert secret not in str([row.event_metadata for row in rows])
    assert "비밀" not in str([row.target_name_snapshot for row in rows])


@pytest.mark.asyncio
async def test_memory_lifecycle_routes_write_audit_without_text(
    client,
    db: AsyncSession,
) -> None:
    secret = "철회할 기억 내용 5521"
    created = await client.post("/api/memories", json={"scope": "user", "content": secret})
    assert created.status_code == 201, created.text
    memory_id = created.json()["id"]
    revoked = await client.post(f"/api/memories/{memory_id}/revoke", json={"reason": "틀림"})
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert (await client.get("/api/memories?status=revoked")).json()[0]["id"] == memory_id
    restored = await client.post(f"/api/memories/{memory_id}/restore")
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    history = await client.get(f"/api/memories/{memory_id}/revisions")
    assert [row["action"] for row in history.json()] == ["create", "revoke", "restore"]
    exported = await client.get("/api/memories/export?format=markdown")
    assert exported.status_code == 200 and secret in exported.text
    count = await client.get("/api/memories/delete-scope/count?scope=all")
    assert count.json() == {"count": 1}
    stale = await client.post(
        "/api/memories/delete-scope", json={"scope": "all", "confirm_count": 5}
    )
    assert stale.status_code in (400, 422)
    deleted = await client.post(
        "/api/memories/delete-scope", json={"scope": "all", "confirm_count": 1}
    )
    assert deleted.status_code == 200 and deleted.json()["deleted"] == 1

    rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.action.like("memory.%"))))
        .scalars()
        .all()
    )
    actions = [row.action for row in rows]
    for action in (
        "memory.record_revoke",
        "memory.record_restore",
        "memory.export",
        "memory.record_delete_bulk",
    ):
        assert action in actions
    by_action = {row.action: row for row in rows}
    revoke = by_action["memory.record_revoke"]
    assert (revoke.target_type, revoke.target_id, revoke.outcome) == (
        "memory_record",
        memory_id,
        "success",
    )
    bulk = by_action["memory.record_delete_bulk"].event_metadata
    assert bulk is not None and (bulk["deleted"], bulk["scope"]) == (1, "all")
    activity = await client.get("/api/me/memory-activity")
    assert activity.status_code == 200
    listed = [row["action"] for row in activity.json()]
    assert listed[0] == "memory.record_delete_bulk" and "memory.record_revoke" in listed
    assert secret not in activity.text
    assert secret not in str([row.event_metadata for row in rows])


@pytest.mark.asyncio
async def test_memory_policy_route_and_mode_limits(client, db: AsyncSession) -> None:
    from app.models.organization import Tenant
    from tests.authz.unit_scope_fixture import TEST_TENANT_ID

    policy = await client.get("/api/me/memory-policy")
    assert policy.status_code == 200, policy.text
    body = policy.json()
    assert (body["tenant_kind"], body["effective_mode"]) == ("company", "approve_all")
    assert set(body["axes"]) == {"records", "profile", "consolidation"}
    saved = await client.patch("/api/me/memory-settings", json={"approval_mode": "partial_auto"})
    assert saved.status_code == 200 and saved.json()["memory_write_policy"] == "auto"
    assert (await client.get("/api/me/memory-policy")).json()["effective_mode"] == "partial_auto"
    tenant_id = TEST_TENANT_ID
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        tenant = Tenant(id=tenant_id, name="t", slug="t", secrets_namespace="t")
        db.add(tenant)
    tenant.settings = {"memory": {"allowed_modes": ["approve_all"]}}
    await db.commit()
    limited = (await client.get("/api/me/memory-policy")).json()
    assert limited["allowed_modes"] == ["approve_all"]
    assert limited["effective_mode"] == "approve_all"  # the stored mode is no longer allowed
    refused = await client.patch("/api/me/memory-settings", json={"approval_mode": "auto_notify"})
    assert refused.status_code in (400, 422)
