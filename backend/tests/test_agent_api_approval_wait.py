"""An Agent API run that waits for the deployment owner's approval inbox (17.3, 순번 21)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.approvals import api_runs, recording, service
from app.authz.org_context import OrgContext
from app.exceptions import NotFoundError
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.approval_request import ApprovalRequest
from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.models.notification import Notification
from tests.authz.g03_support import approval_payload
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_agent_runtime_public_api import _seed_deployed_agent_with_key

pytestmark = pytest.mark.anyio
CONTEXT = OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)
RUN = {"input": {"messages": [{"role": "user", "content": "메일 보내줘"}]}}


async def _deployed(db, *, policy: str = "inbox", scopes: list[Any] | None = None):
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db, scopes=scopes)
    row = await db.get(AgentDeployment, deployment.id)
    row.approval_policy = policy
    await db.commit()
    return agent, row, {"Authorization": f"Bearer {cleartext}"}


def _pausing_invoke(monkeypatch: pytest.MonkeyPatch, seen: dict[str, Any]) -> None:
    """The graph pauses on an approval: invoke returns, the checkpoint holds the interrupt."""

    async def invoke(cfg, messages, **kwargs):
        seen["approval"] = cfg.unattended_approval
        return "승인이 필요합니다"

    async def paused(_conversation_id: uuid.UUID) -> list[service.PendingInterrupt]:
        return [service.PendingInterrupt("intr-api", approval_payload())]

    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_invoke", invoke)
    monkeypatch.setattr(recording, "checkpoint_interrupts", paused)


async def _wait(client, db, monkeypatch, *, policy: str = "inbox"):
    _agent, deployment, headers = await _deployed(db, policy=policy)
    seen: dict[str, Any] = {}
    _pausing_invoke(monkeypatch, seen)
    response = await client.post(
        "/v1/runs/wait", headers=headers, json={"agent_id": deployment.public_id, **RUN}
    )
    assert response.status_code == 200, response.text
    return response.json(), headers, seen


async def _request() -> ApprovalRequest:
    async with TestSession() as db:
        [row] = list(await db.scalars(select(ApprovalRequest)))
        return row


async def _api_run(public_id: str) -> AgentApiRun:
    async with TestSession() as db:
        row = await db.scalar(select(AgentApiRun).where(AgentApiRun.public_id == public_id))
        assert row is not None
        return row


async def _resume(row: ApprovalRequest, *decisions: str, channel: str = "inbox") -> uuid.UUID:
    async with TestSession() as db:
        parent = await db.get(ConversationRun, row.run_id)
        assert parent is not None
        child = ConversationRun(
            conversation_id=parent.conversation_id,
            agent_id=parent.agent_id,
            user_id=parent.user_id,
            parent_run_id=parent.id,
            source="resume",
            status="completed",
            is_active=False,
            tenant_id=parent.tenant_id,
            org_id=parent.org_id,
        )
        db.add(child)
        await db.flush()
        await service.claim(
            db,
            service.Claim(
                parent.id,
                {row.interrupt_id: list(decisions)},
                channel,
                None if channel == "expiry" else TEST_USER_ID,
                resumed_run_id=child.id,
            ),
        )
        await db.commit()
        return child.id


async def test_an_inbox_deployment_answers_waiting_and_asks_its_owner(
    client, db, monkeypatch
) -> None:
    body, _headers, seen = await _wait(client, db, monkeypatch)

    assert seen["approval"] == "wait"
    assert (body["status"], body["output"], body["finished_at"]) == ("waiting", None, None)
    [pending] = body["pending_approvals"]
    assert pending["tools"] == ["execute_in_skill", "send_email"]
    row = await _request()
    assert str(row.id) == pending["id"]
    api_run = await _api_run(body["id"])
    assert (row.source, row.api_run_id, row.decider_user_id) == ("api", api_run.id, TEST_USER_ID)
    async with TestSession() as check:
        run = await check.get(ConversationRun, api_run.conversation_run_id)
        [note] = list(
            await check.scalars(
                select(Notification).where(Notification.kind == "approval.requested")
            )
        )
        audit = await check.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "agent_api.run_wait", AuditEvent.target_id == body["id"]
            )
        )
    assert run is not None and (run.status, run.is_active) == ("interrupted", False)
    assert note.user_id == TEST_USER_ID and note.payload["source"] == "api"
    assert audit is not None and audit.outcome == "success"
    assert audit.event_metadata is not None
    assert audit.event_metadata["awaiting_approval"] is True


async def test_a_blocked_deployment_never_waits(client, db, monkeypatch) -> None:
    body, _headers, seen = await _wait(client, db, monkeypatch, policy="blocked")

    assert seen["approval"] == "block"
    assert body["status"] == "succeeded" and body["pending_approvals"] == []
    assert body["output"] == {"answer": "승인이 필요합니다"}
    async with TestSession() as check:
        assert list(await check.scalars(select(ApprovalRequest))) == []


async def test_the_compatible_endpoints_block_even_for_an_inbox_deployment(
    client, db, monkeypatch
) -> None:
    _agent, deployment, headers = await _deployed(db)
    seen: dict[str, Any] = {}
    _pausing_invoke(monkeypatch, seen)

    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": deployment.public_id, "messages": RUN["input"]["messages"]},
    )

    assert response.status_code == 200, response.text
    assert seen["approval"] == "block"
    assert response.json()["choices"][0]["message"]["content"] == "승인이 필요합니다"


async def test_the_caller_reads_the_answer_once_the_resumed_run_ends(
    client, db, monkeypatch
) -> None:
    body, headers, _seen = await _wait(client, db, monkeypatch)
    waiting = await client.get(f"/v1/runs/{body['id']}", headers=headers)
    assert waiting.status_code == 200 and waiting.json()["status"] == "waiting"
    assert len(waiting.json()["pending_approvals"]) == 1

    async def answer(_db: Any, _tenant: Any, conversation_id: uuid.UUID, secrets: Any) -> str:
        return "메일을 보냈습니다"

    monkeypatch.setattr(api_runs, "final_answer", answer)
    child = await _resume(await _request(), "approve", "approve")
    await api_runs.settle(child, "interrupted", CONTEXT)  # waiting again: no change
    assert (await _api_run(body["id"])).status == "waiting"
    await api_runs.settle(child, "completed", CONTEXT)

    done = (await client.get(f"/v1/runs/{body['id']}", headers=headers)).json()
    assert (done["status"], done["output"]) == ("succeeded", {"answer": "메일을 보냈습니다"})
    assert done["pending_approvals"] == [] and done["error"] is None
    assert done["finished_at"] is not None


async def test_an_expired_request_fails_the_api_run(client, db, monkeypatch) -> None:
    body, headers, _seen = await _wait(client, db, monkeypatch)
    child = await _resume(await _request(), "reject", "reject", channel="expiry")
    async with TestSession() as check:  # the sweep marks it expired as it resumes
        row = await check.get(ApprovalRequest, (await _request()).id)
        assert row is not None
        row.status = "expired"
        await check.commit()

    await api_runs.settle(child, "completed", CONTEXT)

    done = (await client.get(f"/v1/runs/{body['id']}", headers=headers)).json()
    assert done["status"] == "failed"
    assert done["error"]["code"] == api_runs.EXPIRED_CODE


async def test_an_emergency_stop_ends_the_waiting_api_run(client, db, monkeypatch) -> None:
    body, headers, _seen = await _wait(client, db, monkeypatch)
    agent_id = (await _request()).agent_id

    stopped = await client.post(
        "/api/emergency-stops",
        json={"scope_type": "agent", "scope_id": str(agent_id), "reason": "오발송 의심"},
    )

    assert stopped.status_code == 201, stopped.text
    done = (await client.get(f"/v1/runs/{body['id']}", headers=headers)).json()
    assert done["status"] == "failed"
    assert done["error"]["code"] == api_runs.CANCELED_CODE
    assert "emergency_stop" in done["error"]["message"]


async def test_a_request_with_another_pending_one_keeps_the_run_waiting(
    client, db, monkeypatch
) -> None:
    body, _headers, _seen = await _wait(client, db, monkeypatch)
    first = await _request()
    async with TestSession() as check:
        run = await check.get(ConversationRun, first.run_id)
        assert run is not None
        await service.record_pending(
            check,
            run,
            [service.PendingInterrupt("intr-2", approval_payload())],
            origin=service.RunOrigin(source="api", api_run_id=first.api_run_id),
        )
        row = await check.get(ApprovalRequest, first.id)
        assert row is not None
        row.status, row.reason = "canceled", "arguments_changed"
        await check.commit()

    await api_runs.abandon(CONTEXT, [first.id])
    assert (await _api_run(body["id"])).status == "waiting"


async def test_reading_a_run_needs_its_owner_and_a_read_or_invoke_scope(
    client, db, monkeypatch
) -> None:
    body, headers, _seen = await _wait(client, db, monkeypatch)

    missing = await client.get("/v1/runs/run_unknown", headers=headers)
    assert missing.status_code == 404

    from app.agent_api import service as api_service
    from app.schemas.agent_api import AgentApiKeyCreate
    from tests.authz.unit_scope_fixture import unit_context

    api_run = await _api_run(body["id"])
    assert api_run.deployment_id is not None
    _key, stream_only = await api_service.create_api_key(
        db,
        unit_context(TEST_USER_ID),
        AgentApiKeyCreate(
            name="Stream key", scopes=["stream"], deployment_ids=[api_run.deployment_id]
        ),
    )
    refused = await client.get(
        f"/v1/runs/{body['id']}", headers={"Authorization": f"Bearer {stream_only}"}
    )
    assert refused.status_code == 403


async def test_the_answer_is_the_last_message_with_its_secrets_masked(
    client, db, monkeypatch
) -> None:
    from app.agent_runtime import checkpointer

    body, _headers, _seen = await _wait(client, db, monkeypatch)
    conversation_id = (await _api_run(body["id"])).conversation_id
    assert conversation_id is not None

    class Saver:
        async def aget_tuple(self, config: dict[str, Any]) -> Any:
            assert config["configurable"]["thread_id"] == str(conversation_id)
            message = AIMessage(content="보냈습니다 sk-live-123")
            return SimpleNamespace(checkpoint={"channel_values": {"messages": [message]}})

    monkeypatch.setattr(checkpointer, "get_checkpointer", Saver)

    async with TestSession() as check:
        answer = await api_runs.final_answer(check, CONTEXT, conversation_id, ["sk-live-123"])
        # The company gate: another company's thread is not read.
        other = OrgContext(org_id=uuid.uuid4(), tenant_id=uuid.uuid4(), user_id=TEST_USER_ID)
        with pytest.raises(NotFoundError):
            await api_runs.final_answer(check, other, conversation_id, [])

    assert "sk-live-123" not in answer and answer.startswith("보냈습니다")


async def test_the_deployment_policy_is_set_and_audited(client, db) -> None:
    _agent, deployment, _headers = await _deployed(db, policy="blocked")

    created = await client.get("/api/agent-api/deployments")
    assert created.json()[0]["approval_policy"] == "blocked"
    updated = await client.patch(
        f"/api/agent-api/deployments/{deployment.id}", json={"approval_policy": "inbox"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["approval_policy"] == "inbox"
    invalid = await client.patch(
        f"/api/agent-api/deployments/{deployment.id}", json={"approval_policy": "auto"}
    )
    assert invalid.status_code == 422
    async with TestSession() as check:
        audit = await check.scalar(
            select(AuditEvent).where(AuditEvent.action == "agent_api.deployment_update")
        )
    assert audit is not None and audit.event_metadata is not None
    assert audit.event_metadata["approval_policy"] == "inbox"
    assert audit.event_metadata["changed_fields"] == ["approval_policy"]


async def test_the_owner_decides_an_api_request_in_the_inbox(client, db, monkeypatch) -> None:
    """The API conversation is the owner's: the inbox resumes it like a schedule's."""
    from app.routers import conversation_agent_protocol_interrupts as interrupts

    body, _headers, _seen = await _wait(client, db, monkeypatch)
    row = await _request()

    async def pending(*_args: Any) -> list[dict[str, Any]]:
        return [{"id": "intr-api", "value": approval_payload(), "ns": []}]

    started: list[dict[str, Any]] = []

    async def start(**kwargs: Any) -> None:
        started.append(kwargs)

    monkeypatch.setattr(interrupts, "_checkpoint_interrupts", pending)
    monkeypatch.setattr("app.routers.conversation_agent_protocol.start_conversation_run", start)

    decided = await client.post(
        f"/api/approvals/{row.id}/decision",
        json={"decisions": [{"type": "approve"}, {"type": "approve"}]},
    )

    assert decided.status_code == 200, decided.text
    assert (decided.json()["status"], decided.json()["channel"]) == ("approved", "inbox")
    [start_call] = started
    assert start_call["moldy_source"] == "resume"
    assert (await _api_run(body["id"])).status == "waiting"  # until the resumed run ends
