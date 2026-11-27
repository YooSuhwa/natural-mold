"""AG-UI resume inputs may only decide the current server-issued interrupts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from ag_ui.core import ResumeEntry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service as approvals
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.skill_builder_session import SkillBuilderSession
from app.routers.conversation_agent_protocol_consent import apply_session_consent_decisions
from app.routers.conversation_agent_protocol_interrupts import ThreadInterrupt
from app.routers.session_consent_scope import SessionConsentPayload, SessionConsentTarget
from app.services import ag_ui_pending_checkpoint, ag_ui_resume
from app.services.ag_ui_pending_checkpoint import PendingCheckpoint
from tests.authz.g03_support import auth_payload, seed_conversation
from tests.authz.unit_scope_fixture import ensure_unit_scope
from tests.conftest import TEST_USER_ID, override_get_current_user

pytestmark = pytest.mark.asyncio

FIRST = "936c754312aa41f29f9ba2a54ea17390"
SECOND = "7bd37df9b3b544329944bda6092fe8e7"
CHECKPOINT = "1efbe686-5974-6001-8001-9f28cf4621a2"
CHILD_NAMESPACE = ["tools:child-1", "model:agent-1"]


def _approval(name: str, **args: Any) -> dict[str, Any]:
    return {
        "action_requests": [{"name": name, "args": args}],
        "review_configs": [
            {"action_name": name, "allowed_decisions": ["approve", "edit", "reject"]}
        ],
    }


def _slot(
    slot_id: str, value: dict[str, Any], namespace: list[str] | None = None
) -> ThreadInterrupt:
    return {"id": slot_id, "value": value, "ns": namespace or []}


def _resolved(slot_id: str, *decisions: dict[str, Any]) -> ResumeEntry:
    return ResumeEntry(
        interrupt_id=slot_id, status="resolved", payload={"decisions": list(decisions)}
    )


def _cancelled(slot_id: str) -> ResumeEntry:
    return ResumeEntry(interrupt_id=slot_id, status="cancelled")


@dataclass
class PendingRun:
    db: AsyncSession
    conversation: Conversation
    context: OrgContext
    parent: ConversationRun
    rows: dict[str, ApprovalRequest]
    approval_run_ids: dict[str, tuple[UUID, ...]]

    async def verify(self, *entries: ResumeEntry) -> ag_ui_resume.VerifiedResume:
        return await ag_ui_resume.verify_ag_ui_resume(
            self.db, conversation=self.conversation, context=self.context, entries=entries
        )

    async def claim(self, verified: ag_ui_resume.VerifiedResume, child: ConversationRun) -> None:
        await ag_ui_resume.claim_verified_resume(
            self.db, verified=verified, actor_id=self.context.user_id, resumed_run_id=child.id
        )

    async def child(self) -> ConversationRun:
        child = ConversationRun(
            conversation_id=self.conversation.id,
            agent_id=self.conversation.agent_id,
            tenant_id=self.context.tenant_id,
            org_id=self.context.org_id,
            user_id=self.context.user_id,
            source="resume",
            status="queued",
            is_active=True,
            parent_run_id=self.parent.id,
        )
        self.db.add(child)
        await self.db.flush()
        return child


async def _pending(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    *slots: ThreadInterrupt,
    legacy: bool = False,
    recorded: bool = True,
    session_offer: str | None = None,
) -> PendingRun:
    context = await ensure_unit_scope(db, await override_get_current_user())
    conversation = await seed_conversation(db)
    parent = ConversationRun(
        tenant_id=None if legacy else context.tenant_id,
        org_id=None if legacy else context.org_id,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=context.user_id,
        source="chat",
        status="interrupted",
        is_active=False,
        interrupt_id=slots[0]["id"],
    )
    db.add(parent)
    await db.flush()
    rows = (
        []
        if legacy or not recorded
        else await approvals.record_pending(
            db,
            parent,
            [approvals.PendingInterrupt(slot["id"], slot["value"], slot["ns"]) for slot in slots],
            origin=approvals.RunOrigin(),
        )
    )
    await db.commit()
    by_id = {row.interrupt_id: row for row in rows}
    approval_run_ids: dict[str, tuple[UUID, ...]] = {}

    async def read_checkpoint(*args: Any, **kwargs: Any) -> PendingCheckpoint:
        normalized: list[ThreadInterrupt] = []
        kinds: dict[str, str] = {}
        for slot in slots:
            ident = slot["id"]
            kind, value = ag_ui_pending_checkpoint._interrupt_value(ident, slot["value"])  # noqa: SLF001
            if ident == session_offer:
                # Emulate the scoped checkpoint reader's verified journal offer.
                value["review_configs"] = [
                    {**config, "session_consent_eligible": True}
                    for config in value["review_configs"]
                ]
            normalized.append(_slot(ident, value, slot["ns"]))
            kinds[ident] = kind
        return PendingCheckpoint(
            checkpoint_id=CHECKPOINT,
            interrupts=normalized,
            expires_at={key: row.expires_at for key, row in by_id.items()},
            native_kinds=kinds,
            approval_run_ids=approval_run_ids,
        )

    monkeypatch.setattr(ag_ui_resume, "read_pending_checkpoint", read_checkpoint)
    return PendingRun(db, conversation, context, parent, by_id, approval_run_ids)


async def test_mixed_expired_abandonment_and_live_approval_are_claimed_once(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = await _pending(
        db,
        monkeypatch,
        _slot(FIRST, _approval("send_email", to="test@example.test")),
        _slot(SECOND, _approval("create_report", title="Minutes"), CHILD_NAMESPACE),
    )
    expired, live = waiting.rows[FIRST], waiting.rows[SECOND]
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=20)
    await db.commit()

    with pytest.raises(AppError) as stale:
        await waiting.verify(
            _resolved(SECOND, {"type": "approve"}), _resolved(FIRST, {"type": "approve"})
        )
    assert stale.value.status == 409
    assert all(row.status == "pending" for row in waiting.rows.values())

    verified = await waiting.verify(_resolved(SECOND, {"type": "approve"}), _cancelled(FIRST))
    assert verified.checkpoint_id == CHECKPOINT
    assert verified.parent_run.id == waiting.parent.id
    assert set(verified.expired_abandonments) == {FIRST}
    assert verified.input_payload[FIRST]["decisions"][0]["type"] == "reject"
    assert verified.input_payload[SECOND] == {"decisions": [{"type": "approve"}]}
    assert {entry.interrupt_id: entry.namespace for entry in verified.resume.submitted} == {
        FIRST: (),
        SECOND: tuple(CHILD_NAMESPACE),
    }
    child = await waiting.child()
    await waiting.claim(verified, child)
    await db.commit()
    await db.refresh(expired)
    await db.refresh(live)
    assert (expired.status, expired.channel, expired.decisions) == ("expired", "expiry", ["reject"])
    assert (live.status, live.channel, live.decisions) == ("approved", "chat", ["approve"])
    assert expired.resumed_run_id == live.resumed_run_id == child.id
    assert expired.decision_no == live.decision_no == 1
    with pytest.raises(AppError) as replay:
        await waiting.claim(verified, child)
    assert replay.value.status == 409
    await db.rollback()
    await db.refresh(expired)
    await db.refresh(live)
    assert expired.decision_no == live.decision_no == 1


@pytest.mark.parametrize("ids", [(FIRST,), (FIRST, FIRST), (SECOND, "stale-slot")])
async def test_parallel_resume_requires_each_current_sibling_exactly_once(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, ids: tuple[str, ...]
) -> None:
    waiting = await _pending(
        db,
        monkeypatch,
        _slot(FIRST, _approval("mail")),
        _slot(SECOND, _approval("save"), CHILD_NAMESPACE),
    )
    with pytest.raises(AppError) as stale:
        await waiting.verify(*(_resolved(slot_id, {"type": "approve"}) for slot_id in ids))
    assert stale.value.status == 409
    assert all(row.status == "pending" and row.decision_no == 0 for row in waiting.rows.values())


async def test_another_member_cannot_approve_same_organization_chat(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = await _pending(db, monkeypatch, _slot(FIRST, _approval("mail")))
    other = await ensure_unit_scope(
        db, CurrentUser(id=uuid4(), email="peer@example.test", name="Peer")
    )
    await db.commit()
    with pytest.raises(AppError) as denied:
        await ag_ui_resume.verify_ag_ui_resume(
            db,
            conversation=waiting.conversation,
            context=other,
            entries=[_resolved(FIRST, {"type": "approve"})],
        )
    assert denied.value.status in {404, 409}
    verified = await waiting.verify(_resolved(FIRST, {"type": "approve"}))
    child = await waiting.child()
    with pytest.raises(AppError) as wrong_actor:
        await ag_ui_resume.claim_verified_resume(
            db, verified=verified, actor_id=other.user_id, resumed_run_id=child.id
        )
    assert wrong_actor.value.status == 409
    assert waiting.rows[FIRST].status == "pending"


async def test_redacted_edit_uses_original_current_action_and_forbids_tool_rename(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _approval("mail", title="Draft", token="dummy-private-token")
    waiting = await _pending(db, monkeypatch, _slot(FIRST, original))
    args = {"title": "Final", "token": "<redacted>"}
    with pytest.raises(AppError) as renamed:
        await waiting.verify(
            _resolved(FIRST, {"type": "edit", "edited_action": {"name": "erase", "args": args}})
        )
    assert renamed.value.status == 422
    verified = await waiting.verify(
        _resolved(FIRST, {"type": "edit", "edited_action": {"args": args}})
    )
    assert verified.input_payload[FIRST]["decisions"] == [
        {
            "type": "edit",
            "edited_action": {
                "name": "mail",
                "args": {"title": "Final", "token": "dummy-private-token"},
            },
        }
    ]
    child = await waiting.child()
    await waiting.claim(verified, child)
    await db.refresh(waiting.rows[FIRST])
    assert waiting.rows[FIRST].status == "edited"
    assert waiting.rows[FIRST].edited_args_digest


async def test_auth_reask_requires_current_interrupt_id_and_attempt(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = await _pending(
        db, monkeypatch, _slot(SECOND, {**auth_payload(2), "auth_request_id": FIRST})
    )

    def answer(slot_id: str, attempt: int) -> ResumeEntry:
        return _resolved(slot_id, {"type": "respond", "message": "retry", "attempt": attempt})

    for old in (answer(FIRST, 2), answer(SECOND, 1)):
        with pytest.raises(AppError) as stale:
            await waiting.verify(old)
        assert stale.value.status == 409
    with pytest.raises(AppError) as no_attempt:
        await waiting.verify(_resolved(SECOND, {"type": "respond", "message": "retry"}))
    assert no_attempt.value.status == 422
    cancelled = await waiting.verify(_cancelled(SECOND))
    assert cancelled.input_payload == {
        SECOND: {"decisions": [{"type": "respond", "message": "cancel", "attempt": 2}]}
    }
    verified = await waiting.verify(answer(SECOND, 2))
    assert verified.input_payload == {
        SECOND: {"decisions": [{"type": "respond", "message": "retry", "attempt": 2}]}
    }


async def test_question_and_legacy_approval_resume_without_approval_rows(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = await _pending(
        db,
        monkeypatch,
        _slot(FIRST, {"type": "ask_user", "question": "Which?", "options": []}),
        _slot(SECOND, _approval("legacy_tool")),
        legacy=True,
    )
    abandoned = await waiting.verify(_cancelled(FIRST), _cancelled(SECOND))
    assert abandoned.input_payload[FIRST] == {"decisions": [{"type": "respond", "message": ""}]}
    response = {"type": "respond", "message": "Choice 1"}
    verified = await waiting.verify(
        _resolved(FIRST, response), _resolved(SECOND, {"type": "reject"})
    )
    assert not verified.approval_request_ids
    assert verified.input_payload == {
        FIRST: {"decisions": [response]},
        SECOND: {"decisions": [{"type": "reject"}]},
    }
    child = await waiting.child()
    await waiting.claim(verified, child)
    await db.commit()
    assert (
        await db.scalar(
            select(ApprovalRequest.id).where(ApprovalRequest.run_id == waiting.parent.id)
        )
        is None
    )


@pytest.mark.parametrize("kind", ["workflow_wait", "custom_gate"])
async def test_server_owned_or_unknown_interrupt_cannot_be_resolved_by_the_client(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    waiting = await _pending(
        db, monkeypatch, _slot(FIRST, {"type": kind, "workflow_run_id": str(uuid4())})
    )
    for entry in (_resolved(FIRST, {"type": "respond", "message": "complete"}), _cancelled(FIRST)):
        with pytest.raises(AppError) as bad:
            await waiting.verify(entry)
        assert bad.value.status == 422


@pytest.mark.parametrize("payload", [None, {"decisions": [{"type": "approve"}]}])
async def test_cancelled_entry_cannot_carry_an_answer(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    waiting = await _pending(db, monkeypatch, _slot(FIRST, _approval("send_email")))
    entry = ResumeEntry(interrupt_id=FIRST, status="cancelled", payload=payload)
    with pytest.raises(AppError) as bad:
        await waiting.verify(entry)
    assert bad.value.status == 422
    assert waiting.rows[FIRST].status == "pending"


async def test_inbox_deciding_one_sibling_invalidates_the_whole_chat_claim(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = await _pending(
        db, monkeypatch, _slot(FIRST, _approval("send_email")), _slot(SECOND, _approval("archive"))
    )
    verified = await waiting.verify(_resolved(FIRST, {"type": "approve"}), _cancelled(SECOND))
    await approvals.claim(
        db,
        approvals.Claim(
            run_id=waiting.parent.id,
            decisions={SECOND: ["reject"]},
            channel="inbox",
            actor_id=TEST_USER_ID,
        ),
    )
    await db.commit()
    child = await waiting.child()
    with pytest.raises(AppError) as stale:
        await waiting.claim(verified, child)
    assert stale.value.status == 409
    await db.rollback()
    for row in waiting.rows.values():
        await db.refresh(row)
    assert (waiting.rows[FIRST].status, waiting.rows[FIRST].decision_no) == ("pending", 0)
    assert (waiting.rows[SECOND].status, waiting.rows[SECOND].channel) == ("rejected", "inbox")


async def test_abandonment_requires_an_allowed_negative_decision_for_every_action(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = _approval("confirm_send")
    prompt["review_configs"][0]["allowed_decisions"] = ["approve"]
    waiting = await _pending(db, monkeypatch, _slot(FIRST, prompt))
    with pytest.raises(AppError) as invalid:
        await waiting.verify(_cancelled(FIRST))
    assert invalid.value.status == 422
    assert waiting.rows[FIRST].status == "pending"


async def test_verified_approval_cannot_be_claimed_after_its_deadline(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = await _pending(db, monkeypatch, _slot(FIRST, _approval("send_email")))
    verified = await waiting.verify(_resolved(FIRST, {"type": "approve"}))
    row = waiting.rows[FIRST]
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    child = await waiting.child()
    with pytest.raises(AppError) as stale:
        await waiting.claim(verified, child)
    assert stale.value.status == 409
    await db.rollback()
    await db.refresh(row)
    assert (row.status, row.decision_no, row.resumed_run_id) == ("pending", 0, None)


async def test_resume_before_recorder_cannot_approve_a_privacy_degraded_action(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = await _pending(
        db,
        monkeypatch,
        _slot(FIRST, _approval("send_email", token="dummy-private-token")),
        recorded=False,
    )
    # The old worker retained no masked event. Its recovery view only offers rejection.
    with pytest.raises(AppError) as invalid:
        await waiting.verify(_resolved(FIRST, {"type": "approve"}))
    assert invalid.value.status == 422
    await db.rollback()
    await db.refresh(waiting.conversation)
    await db.refresh(waiting.parent)

    safe = await waiting.verify(_cancelled(FIRST))
    row = await db.get(ApprovalRequest, safe.approval_request_ids[FIRST])
    assert row is not None
    assert row.status == "pending"
    assert row.actions[0]["name"] == "<redacted>"
    assert row.actions[0]["allowed_decisions"] == ["reject"]
    child = await waiting.child()
    await waiting.claim(safe, child)
    await db.refresh(row)
    assert (row.status, row.decisions) == ("rejected", ["reject"])


@pytest.mark.parametrize("ancestor_decided", [False, True])
async def test_parallel_slot_surviving_a_resume_must_claim_its_earlier_record(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, ancestor_decided: bool
) -> None:
    payload = _approval("mail", title="Monthly report")
    waiting = await _pending(db, monkeypatch, _slot(FIRST, payload))
    ancestor = ConversationRun(
        conversation_id=waiting.conversation.id,
        agent_id=waiting.conversation.agent_id,
        user_id=TEST_USER_ID,
        org_id=waiting.context.org_id,
        tenant_id=waiting.context.tenant_id,
        source="chat",
        status="interrupted",
        is_active=False,
        interrupt_id=SECOND,
    )
    db.add(ancestor)
    await db.flush()
    waiting.parent.parent_run_id = ancestor.id
    waiting.parent.source = "resume"
    (earlier,) = await approvals.record_pending(
        db, ancestor, [approvals.PendingInterrupt(FIRST, payload)], origin=approvals.RunOrigin()
    )
    await db.commit()
    waiting.approval_run_ids[FIRST] = (ancestor.id, waiting.parent.id)
    verified = await waiting.verify(_resolved(FIRST, {"type": "approve"}))
    assert verified.inherited_approval_request_ids == {FIRST: [earlier.id]}
    assert verified.approval_request_ids == {FIRST: waiting.rows[FIRST].id}

    if ancestor_decided:
        await approvals.claim(
            db,
            approvals.Claim(ancestor.id, {FIRST: ["reject"]}, "inbox", TEST_USER_ID),
        )
        await db.commit()
    child = await waiting.child()
    if ancestor_decided:
        with pytest.raises(AppError) as stale:
            await waiting.claim(verified, child)
        assert stale.value.status == 409
        await db.rollback()
        await db.refresh(earlier)
        await db.refresh(waiting.rows[FIRST])
        assert (earlier.status, earlier.decision_no) == ("rejected", 1)
        assert (waiting.rows[FIRST].status, waiting.rows[FIRST].decision_no) == ("pending", 0)
    else:
        await waiting.claim(verified, child)
        await db.commit()
        for row in (earlier, waiting.rows[FIRST]):
            await db.refresh(row)
            assert (row.status, row.resumed_run_id, row.decision_no) == ("approved", child.id, 1)


@pytest.mark.parametrize(
    ("tool", "offered", "allowed"),
    [
        ("test_skill_draft", True, True),
        ("test_skill_draft", False, False),
        ("finalize_skill", True, False),
    ],
)
async def test_session_consent_requires_a_server_offer_for_an_eligible_tool(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tool: str, offered: bool, allowed: bool
) -> None:
    waiting = await _pending(
        db, monkeypatch, _slot(FIRST, _approval(tool)), session_offer=FIRST if offered else None
    )
    session = SkillBuilderSession(
        conversation_id=waiting.conversation.id,
        tenant_id=waiting.context.tenant_id,
        org_id=waiting.context.org_id,
        user_id=TEST_USER_ID,
        user_request="회의록 스킬",
        status="active",
    )
    db.add(session)
    await db.commit()
    entry = _resolved(FIRST, {"type": "approve", "scope": "session"})
    if not allowed:
        with pytest.raises(AppError) as denied:
            await waiting.verify(entry)
        assert denied.value.status == 422
        await db.refresh(session)
        assert not session.tool_consents
        assert waiting.rows[FIRST].status == "pending"
        return

    verified = await waiting.verify(entry)
    response = verified.input_payload[FIRST]
    assert verified.resume.submitted[0].response is response
    assert response["decisions"] == [{"type": "approve", "scope": "session"}]
    saved = await apply_session_consent_decisions(
        db,
        SessionConsentTarget(waiting.conversation.id, waiting.context),
        SessionConsentPayload(verified.resume, verified.pending_interrupts),
    )
    assert saved == [tool]
    assert verified.input_payload == {FIRST: {"decisions": [{"type": "approve"}]}}
    child = await waiting.child()
    await waiting.claim(verified, child)
    await db.commit()
    await db.refresh(session)
    assert session.tool_consents is not None
    consent = session.tool_consents[tool]
    assert isinstance(consent, dict) and consent["scope"] == "session"
    assert waiting.rows[FIRST].resumed_run_id == child.id


@pytest.mark.parametrize(
    ("original_path", "edited_path", "accepted"),
    [
        ("/agents/known/AGENTS.md", "/conversations/demo/report.md", False),
        ("/conversations/demo/report.md", "/agents/known/AGENTS.md", False),
        ("/conversations/demo/report.md", "relative.md", False),
        ("/conversations/demo/report.md", "<redacted>", True),
    ],
)
async def test_native_approval_edits_cannot_change_a_governed_memo(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    original_path: str,
    edited_path: str,
    accepted: bool,
) -> None:
    waiting = await _pending(
        db, monkeypatch, _slot(FIRST, _approval("edit_file", file_path=original_path))
    )
    answer = _resolved(
        FIRST,
        {"type": "edit", "edited_action": {"args": {"file_path": edited_path}}},
    )
    if accepted:
        verified = await waiting.verify(answer)
        edited = verified.input_payload[FIRST]["decisions"][0]["edited_action"]
        assert edited == {"name": "edit_file", "args": {"file_path": original_path}}
    else:
        with pytest.raises(AppError) as denied:
            await waiting.verify(answer)
        assert denied.value.status == 422
        assert waiting.rows[FIRST].status == "pending"
