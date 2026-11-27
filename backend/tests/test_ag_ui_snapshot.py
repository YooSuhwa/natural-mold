"""A snapshot reports only the selected, currently authorized public state."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from ag_ui.core import ActivityMessage, AssistantMessage
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.message_utils import parse_msg_id
from app.authz.org_context import OrgContext
from app.exceptions import ConflictError, NotFoundError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.schemas.ag_ui_snapshot import AgUiSnapshot
from app.services import ag_ui_snapshot as api
from app.services.ag_ui_read_cursor import CursorScope
from app.services.ag_ui_read_pending import CurrentReadInterrupts
from app.services.ag_ui_snapshot_history import SnapshotHistory, SnapshotMessageRef
from app.services.ag_ui_snapshot_journal import SnapshotRunReplay
from app.services.ag_ui_snapshot_runs import SnapshotRuns


@dataclass
class Case:
    db: AsyncSession
    conversation: Conversation
    context: OrgContext
    history: SnapshotHistory
    run: ConversationRun
    selection: SnapshotRuns
    replay: SnapshotRunReplay
    resolve: AsyncMock
    pending: AsyncMock

    async def load(
        self,
        *,
        run_id: UUID | None = None,
        public_run_id: str | None = None,
        cursor: str | None = None,
    ) -> AgUiSnapshot:
        return await api.load_ag_ui_snapshot(
            self.db,
            self.conversation,
            self.context,
            run_id=run_id,
            public_run_id=public_run_id,
            cursor=cursor,
        )


@pytest.fixture
def case(monkeypatch: pytest.MonkeyPatch) -> Case:
    cid, rid = uuid4(), uuid4()
    context = OrgContext(uuid4(), uuid4(), uuid4())
    conv = Conversation(id=cid, user_id=context.user_id, message_timestamps={})
    run = ConversationRun(id=rid, conversation_id=cid, status="running", is_active=True)
    entries = (
        SnapshotMessageRef(SystemMessage(content="private model instruction"), 0, "before", None),
        SnapshotMessageRef(
            HumanMessage(id="question", content="question"), 1, "question-cp", "before"
        ),
        SnapshotMessageRef(AIMessage(id="answer", content="saved"), 2, "answer-cp", "question-cp"),
    )
    history = SnapshotHistory(
        "answer-cp", entries, "checkpoint", checkpoint_run_ids={"answer-cp": rid}
    )
    runs = SnapshotRuns([run], {"answer": rid}, {rid: "public-r"}, {rid}, False)
    visibility = ChildVisibility(False, frozenset(), frozenset())
    scope = CursorScope(str(cid), "public-r", str(rid), "1", "selected")
    replay = SnapshotRunReplay(
        visibility=visibility,
        scope=scope,
        messages=[{"id": "answer", "role": "assistant", "content": "saved"}],
        cursor="folded-watermark",
        complete=True,
    )
    target = SimpleNamespace(
        run=run,
        scope=scope,
        widget_bindings=frozenset(),
        input_id=None,
        input_status=None,
        revision=None,
        position=None,
    )
    resolve = AsyncMock(return_value=target)
    pending = AsyncMock(return_value=CurrentReadInterrupts())
    monkeypatch.setattr(api, "require_private_conversation", AsyncMock(return_value=conv))
    monkeypatch.setattr(api, "resolve_read_target", resolve)
    monkeypatch.setattr(api, "load_snapshot_history", AsyncMock(return_value=history))
    monkeypatch.setattr(
        api, "collect_conversation_secret_values", AsyncMock(return_value={"CANARY-SUPER-SECRET"})
    )
    monkeypatch.setattr(api, "snapshot_runs", AsyncMock(return_value=runs))
    monkeypatch.setattr(api, "replay_snapshot_run", AsyncMock(return_value=replay))
    monkeypatch.setattr(api, "read_proven_interrupts", pending)
    return Case(
        AsyncSession(),
        conv,
        context,
        history,
        run,
        runs,
        replay,
        resolve,
        pending,
    )


@pytest.mark.asyncio
async def test_checkpoint_root_has_real_controls_and_new_parser_can_drop_only_active_output(
    case: Case,
) -> None:
    view = await case.load(cursor="foreign-watermark")
    assert [m.id for m in view.messages] == ["question", "answer"]
    assert isinstance(view.messages[-1], AssistantMessage)
    assert view.active_tip_message_id == "answer" and view.cursor == "folded-watermark"
    assert view.active_run_id == "public-r" and view.physical_run_id == str(case.run.id)
    assert view.selected_physical_run_id == str(case.run.id)
    assert view.interrupt_run_id is None
    assert view.message_links[-1].ui_message_id == str(
        parse_msg_id("answer", case.conversation.id, 2)
    )
    assert view.message_links[-1].parent_checkpoint_id == "question-cp"
    assert [
        link.message_id for link in view.message_links if link.run_id != view.active_run_id
    ] == ["question"]
    wire = view.model_dump(mode="json", by_alias=True)
    assert "rawEvent" not in str(wire) and "private model" not in str(wire)
    assert wire["messages"][-1] == {"id": "answer", "role": "assistant", "content": "saved"}
    assert wire["interruptRunId"] is None


@pytest.mark.asyncio
async def test_invalidated_prefix_cannot_echo_an_untrusted_cursor(case: Case) -> None:
    case.replay.complete = False
    view = await case.load(cursor="copied-from-another-run")
    assert view.cursor is None
    assert view.messages[-1].id == "answer"


@pytest.mark.asyncio
async def test_transient_change_to_current_child_or_attachment_rights_invalidates_snapshot(
    case: Case,
) -> None:
    case.resolve.return_value.scope = CursorScope(
        str(case.conversation.id), "public-r", str(case.run.id), "1", "revoked"
    )
    with pytest.raises(ConflictError, match="스냅샷"):
        await case.load()


@pytest.mark.asyncio
async def test_only_confirmed_pending_slots_survive_with_true_owners(case: Case) -> None:
    case.run.status, case.run.is_active = "interrupted", False
    ns = ("tools:child",)
    case.replay.child_namespaces[ns] = "public-child"
    case.replay.pending_namespaces.update({"known": ns})
    case.replay.unresolved_pending_ids.add("revoked")
    case.pending.return_value = SimpleNamespace(
        actionable=True,
        legacy_child_ids=frozenset(),
        legacy_bindings={},
        checkpoint_id="answer-cp",
        expires_at={},
        interrupts=[
            {"id": "known", "value": {"type": "ask_user", "question": "Choose"}, "ns": []},
            {"id": "removed", "value": {"type": "workflow_wait"}, "ns": ["unknown-ns"]},
            {"id": "revoked", "value": {"type": "ask_user", "question": "hidden"}, "ns": []},
            {"id": "root", "value": {"type": "workflow_wait"}, "ns": []},
        ],
    )
    view = await case.load()
    assert view.active_run_id is None and view.physical_run_id is None
    assert view.interrupt_run_id == "public-r" and view.physical_interrupt_run_id == str(
        case.run.id
    )
    assert [(i.id, i.subagent_run_id) for i in view.interrupts] == [
        ("known", "public-child"),
        ("root", None),
    ]
    assert all((i.metadata or {})["moldy"]["actionable"] is False for i in view.interrupts)
    assert all(i.response_schema is None for i in view.interrupts)
    case.pending.return_value.checkpoint_id = "stale-head"
    assert not (await case.load()).interrupts


@pytest.mark.asyncio
async def test_independent_checkpoint_keys_are_scoped_and_f14_metadata_is_typed(case: Case) -> None:
    case.run.status, case.run.is_active = "failed", False
    case.run.error_code = "partial_model_stream"
    case.run.metadata_json = {
        "partial_model_output": {
            "text": "CANARY-SUPER-SECRET",
            "tools": [{"name": "lookup", "state": "unknown"}],
            "truncated": False,
        },
        "private": "provider internals",
    }
    object.__setattr__(case.selection, "independent", True)
    view = await case.load()
    assert [m.id for m in view.messages] == ["answer"]
    assert view.message_links[0].parent_checkpoint_id is None
    assert view.failed_physical_run_id == str(case.run.id) and view.partial_output
    assert view.partial_output.text != "CANARY-SUPER-SECRET"
    assert view.partial_output.tools[0].state == "unknown"
    assert "provider internals" not in view.model_dump_json()
    case.run.error_code = "never_publish_this_value"
    new_view = await case.load()
    assert new_view.error_code == "run_failed" and new_view.partial_output is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["recovery_steer_unconfirmed", "recovery_model_attempt_limit"])
async def test_snapshot_keeps_the_reason_a_failed_input_cannot_be_retried(
    case: Case, code: str
) -> None:
    case.run.status = "failed"
    case.run.is_active = False
    case.run.error_code = code
    case.run.error_message = "private provider diagnostic"
    view = await case.load()
    assert view.error_code == code and view.partial_output is None
    assert "private provider diagnostic" not in view.model_dump_json()


@pytest.mark.asyncio
async def test_requested_sibling_refused_and_queue_not_active(case: Case) -> None:
    with pytest.raises(NotFoundError):
        await case.load(run_id=uuid4())
    target = SimpleNamespace(
        run=None, input_id=None, input_status="pending", revision=1, position=2, scope=None
    )
    case.resolve.side_effect = [target, case.resolve.return_value, target]
    queued = await case.load(public_run_id="queue-receipt")
    assert queued.run_id == "queue-receipt" and queued.position == 2
    assert queued.selected_physical_run_id is None
    assert queued.active_run_id is None and queued.physical_run_id is None and queued.cursor is None


@pytest.mark.asyncio
async def test_live_legacy_child_requires_shared_post_authority(case: Case) -> None:
    case.run.status, case.run.is_active = "interrupted", False
    ns = ("tools:legacy",)
    case.replay.child_namespaces[ns] = "child"
    case.replay.subagents.append(
        {
            "subagentRunId": "child",
            "parentMessageId": "answer",
            "name": "research",
            "status": "running",
        }
    )
    case.pending.return_value = SimpleNamespace(
        actionable=True,
        legacy_child_ids=frozenset(),
        legacy_bindings={},
        checkpoint_id="answer-cp",
        expires_at={},
        interrupts=[
            {"id": "live", "ns": list(ns), "value": {"type": "ask_user", "question": "Choose"}}
        ],
    )
    case.pending.return_value.legacy_child_ids = frozenset({"live"})
    view = await case.load()
    assert view.subagents[0].legacy_interrupt_access
    assert view.subagents[0].status == "interrupted" and view.subagents[0].interrupt_ids == ["live"]
    assert (view.interrupts[0].metadata or {})["moldy"]["legacyInterruptAccess"] is True
    case.pending.return_value.actionable = False
    case.pending.return_value.legacy_child_ids = frozenset()
    denied = await case.load()
    assert not denied.subagents[0].legacy_interrupt_access
    assert (denied.interrupts[0].metadata or {})["moldy"]["actionable"] is False
    assert denied.interrupts[0].response_schema is None


@pytest.mark.asyncio
async def test_snapshot_refuses_a_legacy_grant_revoked_before_delivery(case: Case) -> None:
    case.run.status, case.run.is_active = "interrupted", False
    first = CurrentReadInterrupts(checkpoint_id="answer-cp", actionable=True)
    revoked = CurrentReadInterrupts(checkpoint_id="answer-cp", actionable=False)
    case.pending.side_effect = [first, revoked]
    with pytest.raises(ConflictError):
        await case.load()


@pytest.mark.asyncio
async def test_past_child_outcomes_do_not_reintroduce_interrupt_identifiers(case: Case) -> None:
    case.run.status, case.run.is_active = "completed", False
    case.replay.subagents.append(
        {
            "subagentRunId": "old-child",
            "name": "research",
            "status": "interrupted",
            "interruptIds": ["stale-approval"],
            "parentMessageId": "answer",
        }
    )
    view = await case.load()
    assert view.subagents[0].interrupt_ids == []
    assert not view.subagents[0].legacy_interrupt_access
    assert "stale-approval" not in view.model_dump_json()


@pytest.mark.asyncio
async def test_explicit_historical_receipt_keeps_its_own_physical_identity(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case.run.status, case.run.is_active = "completed", False
    later = ConversationRun(
        id=uuid4(), conversation_id=case.conversation.id, status="running", is_active=True
    )
    later_scope = CursorScope(
        str(case.conversation.id), "public-later", str(later.id), "1", "selected"
    )
    case.selection.candidates.append(later)
    case.selection.public_ids[later.id] = "public-later"
    case.selection.branch_verified.add(later.id)
    later_replay = SnapshotRunReplay(visibility=case.replay.visibility, scope=later_scope)
    replay = AsyncMock(side_effect=[case.replay, later_replay])
    monkeypatch.setattr(api, "replay_snapshot_run", replay)
    first = case.resolve.return_value
    last = SimpleNamespace(run=later, scope=later_scope, widget_bindings=frozenset())
    case.resolve.side_effect = [first, first, last]
    view = await case.load(public_run_id="public-r")
    assert (view.run_id, view.run_status, view.selected_physical_run_id) == (
        "public-r",
        "completed",
        str(case.run.id),
    )
    assert (view.active_run_id, view.physical_run_id, view.latest_physical_run_id) == (
        "public-later",
        str(later.id),
        str(later.id),
    )


@pytest.mark.asyncio
async def test_checkpoint_widget_uses_final_physical_binding_and_public_issuer(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call, binding = "raw-weather-call", str(uuid4())
    physical = str(case.run.id)
    issuer = AIMessage(
        id="weather-question",
        content="",
        tool_calls=[{"name": "show_weather", "id": call, "args": {}}],
    )
    result = ToolMessage(
        id="weather-result",
        name="show_weather",
        tool_call_id=call,
        content="Weather",
        artifact={
            "mcp_app": {
                "version": 1,
                "binding_id": binding,
                "run_id": physical,
                "resource_uri": "ui://private/weather",
                "tool_meta": {},
            },
            "structured_content": {"privateData": "private weather payload"},
        },
    )
    history = SnapshotHistory(
        "result-cp",
        (
            SnapshotMessageRef(issuer, 0, "issuer-cp", None),
            SnapshotMessageRef(result, 1, "result-cp", "issuer-cp"),
        ),
        "checkpoint",
        ("issuer-cp", "result-cp"),
        checkpoint_run_ids={"issuer-cp": case.run.id, "result-cp": case.run.id},
    )
    monkeypatch.setattr(api, "load_snapshot_history", AsyncMock(return_value=history))
    case.selection.message_runs.update(
        {"weather-question": case.run.id, "weather-result": case.run.id}
    )
    case.replay.messages = []
    case.replay.complete = False
    case.resolve.return_value.widget_bindings = frozenset({(call, binding)})

    view = await case.load()
    cards = [m for m in view.messages if isinstance(m, ActivityMessage)]
    assert len(cards) == 1
    card = cards[0]
    author = next(m for m in view.messages if isinstance(m, AssistantMessage))
    assert author.tool_calls
    public_call = author.tool_calls[0].id
    assert call != public_call
    assert card.activity_type == "moldy.mcp_app"
    assert card.content["run_id"] == physical and card.content["binding_id"] == binding
    assert (card.content["tool_call_id"], card.content["public_tool_call_id"]) == (
        call,
        public_call,
    )
    pointer = next(link for link in view.message_links if link.message_id == card.id)
    assert (pointer.parent_message_id, pointer.parent_tool_call_id) == (author.id, public_call)
    assert (
        "private weather payload" not in view.model_dump_json()
        and "ui://private" not in view.model_dump_json()
    )

    case.resolve.return_value.widget_bindings = frozenset()
    withdrawn = await case.load()
    assert not any(isinstance(m, ActivityMessage) for m in withdrawn.messages)
    assert binding not in withdrawn.model_dump_json()
