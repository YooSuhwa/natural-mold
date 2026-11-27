import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal
from uuid import uuid4

import pytest

from app.agent_runtime import ag_ui_v1_children, ag_ui_v1_issuance
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.approvals.service import digest
from app.models.conversation_run import ConversationRun
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_approval_decisions import match_approval_decisions
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot, Row
from app.services.ag_ui_snapshot_decision_ledger import (
    ConsumedApprovalAction,
    ConsumedApprovalDecision,
)

NAMESPACE = ("tools:delegated-review",)
SOURCE = "original-approval"
RESUME = "accepted-response"
PRIVATE = "private-call-argument-fixture"
NAME = "lookup"
type Decision = tuple[str, dict[str, str], Literal["approve", "edit", "reject"]]


@dataclass
class Transcript:
    parent: ConversationRun
    resumed: ConversationRun
    merged: AssembledSnapshot = field(
        default_factory=lambda: AssembledSnapshot([], [], [], {}, None, set())
    )
    continuations: list[ag_ui_v1_issuance.ToolContinuation] = field(default_factory=list)
    owners: dict[str, str | None] = field(default_factory=dict)

    @property
    def thread(self) -> str:
        return str(self.parent.conversation_id)

    @property
    def child(self) -> str:
        return ag_ui_v1_children.public_subagent_id(self.thread, NAMESPACE)

    def issuer(
        self,
        mid: str,
        args: dict[str, str],
        *,
        owner: str | None = None,
        name: str = NAME,
        raw: str = "provider-private-id",
    ) -> str:
        cid = ag_ui_v1_issuance.public_tool_call_id(SOURCE, owner, mid, raw)
        row: Row = {
            "id": mid,
            "role": "assistant",
            "toolCalls": [
                {
                    "id": cid,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        }
        if owner:
            row["subagentRunId"] = owner
        self.merged.messages.append(row)
        self.merged.message_links.append(
            AgUiSnapshotMessageLink(
                message_id=mid,
                physical_run_id=str(self.parent.id),
                run_id=SOURCE,
                subagent_run_id=owner,
            )
        )
        self.continuations.append(
            ag_ui_v1_issuance.ToolContinuation(
                owner=owner,
                raw_id=raw,
                name=name,
                public_id=cid,
                parent_message_id=mid,
                source_message_id=mid,
            )
        )
        return cid

    def ledger(
        self,
        iid: str,
        decisions: Sequence[Decision],
        *,
        child: bool = False,
    ) -> ConsumedApprovalDecision:
        self.owners[iid] = self.child if child else None
        return ConsumedApprovalDecision(
            parent_run_id=self.parent.id,
            resumed_run_id=self.resumed.id,
            source_run_ids=(self.parent.id,),
            interrupt_id=iid,
            namespace=NAMESPACE if child else (),
            args_digest=digest([[name, args] for name, args, _ in decisions]),
            actions=tuple(
                ConsumedApprovalAction(
                    index=i, tool_name=name, decision=decision, display_args_digest=digest(args)
                )
                for i, (name, args, decision) in enumerate(decisions)
            ),
        )

    def decisions(self, *items: ConsumedApprovalDecision, revoked: bool = False) -> list[Row]:
        visibility = ChildVisibility(
            False, frozenset(), frozenset() if revoked else frozenset({NAMESPACE})
        )
        result = match_approval_decisions(
            items,
            thread_id=self.thread,
            merged=self.merged,
            runs={self.parent.id: self.parent, self.resumed.id: self.resumed},
            public_run_ids={self.parent.id: SOURCE, self.resumed.id: RESUME},
            visibility={self.parent.id: visibility, self.resumed.id: visibility},
            continuations={self.resumed.id: tuple(self.continuations)},
            interrupt_owners={self.parent.id: self.owners},
        )
        return [row.model_dump(mode="json", by_alias=True, exclude_none=True) for row in result]


@pytest.fixture
def transcript() -> Transcript:
    conversation, user, org, tenant = (uuid4() for _ in range(4))
    common = {"conversation_id": conversation, "user_id": user, "org_id": org, "tenant_id": tenant}
    parent = ConversationRun(id=uuid4(), source="chat", status="interrupted", **common)
    resumed = ConversationRun(
        id=uuid4(), parent_run_id=parent.id, source="resume", status="completed", **common
    )
    scene = Transcript(parent, resumed)
    scene.merged.subagents.append(
        {
            "subagentRunId": scene.child,
            "name": "worker",
            "status": "completed",
            "runId": SOURCE,
            "physicalRunId": str(parent.id),
        }
    )
    return scene


def test_root_and_child_same_tool_and_original_args_receive_their_own_decision(
    transcript: Transcript,
) -> None:
    args = {"document": PRIVATE}
    root = transcript.issuer("root-issuer", args)
    child = transcript.issuer("child-issuer", args, owner=transcript.child)
    consumed_root = transcript.ledger("root-interrupt", [(NAME, args, "approve")])
    consumed_child = transcript.ledger("child-interrupt", [(NAME, args, "reject")], child=True)
    result = transcript.decisions(consumed_child, consumed_root)
    rows = {row["interruptId"]: row for row in result}
    assert rows == {
        "root-interrupt": {
            "messageId": "root-issuer",
            "toolCallId": root,
            "physicalRunId": str(transcript.parent.id),
            "interruptId": "root-interrupt",
            "actionIndex": 0,
            "decision": "approve",
        },
        "child-interrupt": {
            "messageId": "child-issuer",
            "toolCallId": child,
            "physicalRunId": str(transcript.parent.id),
            "interruptId": "child-interrupt",
            "actionIndex": 0,
            "decision": "reject",
            "subagentRunId": transcript.child,
        },
    }
    serialized = json.dumps(rows)
    assert PRIVATE not in serialized
    assert "provider-private-id" not in serialized
    assert consumed_root.args_digest not in serialized


def test_same_name_actions_use_original_argument_digests_and_persisted_action_order(
    transcript: Transcript,
) -> None:
    one, two = {"value": "first-original"}, {"value": "second-original"}
    second_id = transcript.issuer("second", two, raw="call-b")
    first_id = transcript.issuer("first", one, raw="call-a")
    decided = transcript.ledger("two-actions", [(NAME, one, "edit"), (NAME, two, "reject")])
    result = sorted(transcript.decisions(decided), key=lambda row: row["actionIndex"])
    assert [(row["toolCallId"], row["actionIndex"], row["decision"]) for row in result] == [
        (first_id, 0, "edit"),
        (second_id, 1, "reject"),
    ]


def test_identical_parallel_candidates_are_ambiguous_even_with_one_tool_name_and_digest(
    transcript: Transcript,
) -> None:
    original = {"value": "same"}
    transcript.issuer("parallel-a", original, raw="a")
    transcript.issuer("parallel-b", original, raw="b")
    consumed = transcript.ledger("ambiguous", [(NAME, original, "approve")])
    assert transcript.decisions(consumed) == []


@pytest.mark.parametrize(
    "mismatch",
    [
        "wrong-public-binding",
        "wrong-physical-binding",
        "missing-source",
        "other-branch",
        "wrong-continuation-parent",
        "missing-owner",
        "other-owner",
        "not-assistant",
        "proposal-digest",
    ],
)
def test_mismatched_ownership_or_proposal_never_attaches_a_decision(
    transcript: Transcript, mismatch: str
) -> None:
    args = {"value": PRIVATE}
    transcript.issuer("issuer", args)
    consumed = transcript.ledger("interrupt", [(NAME, args, "approve")])
    link = transcript.merged.message_links[0]
    if mismatch == "wrong-public-binding":
        link.run_id = "another-public-run"
    elif mismatch == "wrong-physical-binding":
        link.physical_run_id = str(transcript.resumed.id)
    elif mismatch == "missing-source":
        consumed = replace(consumed, source_run_ids=(uuid4(),))
    elif mismatch == "other-branch":
        transcript.resumed.parent_run_id = uuid4()
    elif mismatch == "wrong-continuation-parent":
        transcript.continuations[0] = replace(
            transcript.continuations[0], parent_message_id="different-issuer"
        )
    elif mismatch == "missing-owner":
        transcript.owners.clear()
    elif mismatch == "other-owner":
        transcript.owners["interrupt"] = transcript.child
    elif mismatch == "not-assistant":
        transcript.merged.messages[0]["role"] = "user"
    elif mismatch == "proposal-digest":
        consumed = replace(consumed, args_digest=digest([[NAME, {"value": "different proposal"}]]))
    assert transcript.decisions(consumed) == []


def test_revoked_child_and_sibling_namespace_do_not_authorize_historical_decision(
    transcript: Transcript,
) -> None:
    args = {"query": PRIVATE}
    transcript.issuer("agent", args, owner=transcript.child)
    consumed = transcript.ledger("child", [(NAME, args, "edit")], child=True)
    assert transcript.decisions(consumed, revoked=True) == []
    assert transcript.decisions(replace(consumed, namespace=("tools:sibling",))) == []


def test_incomplete_multi_action_proof_cannot_restore_just_the_first_action(
    transcript: Transcript,
) -> None:
    one, two = {"v": "first"}, {"v": "second"}
    transcript.issuer("item-one", one)
    consumed = transcript.ledger("two-required", [(NAME, one, "approve"), (NAME, two, "reject")])
    assert transcript.decisions(consumed) == []


def test_conflicting_history_or_multiple_claims_cannot_assign_two_decisions_to_one_call(
    transcript: Transcript,
) -> None:
    args = {"v": PRIVATE}
    transcript.issuer("only", args)
    original = transcript.ledger("interrupt", [(NAME, args, "approve")])
    conflicting = replace(original, actions=(replace(original.actions[0], decision="edit"),))
    assert transcript.decisions(original, conflicting) == []
    assert len(transcript.decisions(original, original)) == 1
    # An unrelated raw identifier cannot authenticate the original public handle.
    transcript.continuations[0] = replace(
        transcript.continuations[0], raw_id="conflicting-private-issuance"
    )
    assert transcript.decisions(original) == []


def test_only_a_consistently_typed_observed_approval_can_supply_a_root_or_child_actor() -> None:
    from app.agent_runtime.ag_ui_v1 import public_subagent_id

    from app.agent_runtime.protocol_events import stored_protocol_event
    from app.services.ag_ui_journal import JournalEntry, JournalPage
    from app.services.ag_ui_snapshot_journal import SnapshotRunReplay, _ObservedOwners

    namespace = ("tools:delegated",)
    child = public_subagent_id("thread", namespace)
    approval = {"action_requests": [{"name": "search", "args": {}}], "review_configs": []}
    question = {"type": "ask_user", "question": "Which entry?"}
    sources = [
        ("root", (), approval),
        ("child", namespace, approval),
        ("child", (), approval),
        ("question", (), question),
        ("conflict", (), approval),
        ("conflict", (), question),
        ("revoked-child", ("tools:hidden",), approval),
        ("revoked-child", (), approval),
    ]
    observed = _ObservedOwners()
    for seq, (slot, ns, value) in enumerate(sources, start=1):
        event = stored_protocol_event(
            run_id="physical",
            thread_id="thread",
            seq=seq,
            method="input.requested",
            namespace=list(ns),
            data={"interrupt_id": slot, "payload": value},
        )
        observed.observe(
            JournalPage("g", (JournalEntry(seq, str(seq), (event,)),), False, False, "interrupted")
        )
    replay = SnapshotRunReplay(
        visibility=ChildVisibility(False, frozenset(), frozenset({namespace})),
        subagents=[{"subagentRunId": child}],
    )
    observed.bind(replay, "thread", {})
    assert replay.approval_interrupt_owners == {"root": None, "child": child}
