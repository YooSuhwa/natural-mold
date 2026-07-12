from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
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
    assert {row.action for row in rows} >= {
        "agent.create",
        "agent.update",
        "agent.delete",
    }
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
    assert ok_row.event_metadata["transport"] == "streamable_http"
    assert ok_row.event_metadata["tool_count"] == 1
    assert fail_row.outcome == "failure"
    assert fail_row.reason_code == "mcp_probe_failed"
    assert fail_row.reason_message == "boom"
    assert fail_row.event_metadata["tool_count"] == 0


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
            "schedule_config": {"interval_minutes": 10},
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
