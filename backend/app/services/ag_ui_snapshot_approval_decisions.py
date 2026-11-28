"""Restore approval outcomes only on the exact authorized tool issuance that was resumed."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_v1 import public_subagent_id
from app.agent_runtime.ag_ui_v1_issuance import ToolContinuation, public_tool_call_id
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.approvals.service import digest
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.schemas.ag_ui_snapshot import AgUiApprovalDecision
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot
from app.services.ag_ui_snapshot_decision_ledger import (
    ConsumedApprovalDecision,
    load_consumed_approval_decisions,
)
from app.services.ag_ui_snapshot_history import SnapshotHistory


@dataclass(frozen=True, slots=True)
class _Issuer:
    message_id: str
    call_id: str
    name: str
    physical: UUID
    owner: str | None
    arguments: dict[str, Any]
    argument_digest: str


def _visible_issuers(
    merged: AssembledSnapshot,
    public_run_ids: Mapping[UUID, str],
    needs: set[str],
) -> dict[str, _Issuer]:
    by_call: dict[str, _Issuer] = {}
    seen: set[str] = set()
    for message, link in zip(merged.messages, merged.message_links, strict=True):
        if message.get("role") != "assistant" or link.message_id != message.get("id"):
            continue
        if (
            not link.physical_run_id
            or not link.run_id
            or message.get("subagentRunId") != link.subagent_run_id
        ):
            continue
        try:
            physical = UUID(link.physical_run_id)
        except ValueError:
            continue
        if public_run_ids.get(physical) != link.run_id:
            continue
        for call in message.get("toolCalls", []):
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            if not isinstance(call_id, str) or call_id not in needs:
                continue
            if call_id in seen:
                by_call.pop(call_id, None)
                continue
            seen.add(call_id)
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            name, encoded = function.get("name"), function.get("arguments")
            if not isinstance(name, str) or not name or not isinstance(encoded, str):
                continue
            try:
                arguments = orjson.loads(encoded)
            except (ValueError, RecursionError):
                continue
            if isinstance(arguments, dict):
                by_call[call_id] = _Issuer(
                    link.message_id,
                    call_id,
                    name,
                    physical,
                    link.subagent_run_id,
                    arguments,
                    digest(arguments),
                )
    return by_call


def _owns_approval(
    choice: ConsumedApprovalDecision,
    issuer: _Issuer,
    *,
    thread: str,
    children: set[str],
    visibility: Mapping[UUID, ChildVisibility],
    owners: Mapping[UUID, Mapping[str, str | None]],
) -> bool:
    observed = owners.get(issuer.physical, {})
    if choice.interrupt_id not in observed or observed[choice.interrupt_id] != issuer.owner:
        return False
    granted = visibility.get(issuer.physical)
    if granted is None:
        return False
    if issuer.owner is None:
        return not choice.namespace
    if issuer.owner not in children:
        return False
    # The native slot may include a middleware suffix after the actual child namespace.
    # In older records it is also echoed without a namespace in the root inbox record.
    if choice.namespace and not any(
        public_subagent_id(thread, choice.namespace[:i]) == issuer.owner
        for i in range(1, len(choice.namespace) + 1)
    ):
        return False
    return granted.allow_legacy_details or any(
        public_subagent_id(thread, namespace) == issuer.owner for namespace in granted.namespaces
    )


def _match_batch(
    choice: ConsumedApprovalDecision, candidates: Sequence[_Issuer]
) -> list[AgUiApprovalDecision]:
    selected: list[_Issuer] = []
    for expected, action in enumerate(choice.actions):
        if action.index != expected or action.decision not in {"approve", "edit", "reject"}:
            return []
        matches = [
            issuer
            for issuer in candidates
            if issuer.name == action.tool_name
            and (
                action.display_args_digest is None
                or issuer.argument_digest == action.display_args_digest
            )
            and (
                len(choice.actions) != 1
                or digest([[issuer.name, issuer.arguments]]) == choice.args_digest
            )
        ]
        if len(matches) != 1:
            return []
        selected.append(matches[0])
    if len({item.call_id for item in selected}) != len(selected):
        return []
    # Display args can have been masked or compacted. The original, ordered
    # approval digest attests the entire recovered batch before publishing a choice.
    if digest([[item.name, item.arguments] for item in selected]) != choice.args_digest:
        return []
    return [
        AgUiApprovalDecision(
            message_id=item.message_id,
            tool_call_id=item.call_id,
            physical_run_id=str(item.physical),
            interrupt_id=choice.interrupt_id,
            action_index=action.index,
            decision=action.decision,
            subagent_run_id=item.owner,
        )
        for action, item in zip(choice.actions, selected, strict=True)
    ]


def match_approval_decisions(
    ledger: Sequence[ConsumedApprovalDecision],
    *,
    thread_id: str,
    merged: AssembledSnapshot,
    runs: Mapping[UUID, ConversationRun],
    public_run_ids: Mapping[UUID, str],
    visibility: Mapping[UUID, ChildVisibility],
    continuations: Mapping[UUID, tuple[ToolContinuation, ...]],
    interrupt_owners: Mapping[UUID, Mapping[str, str | None]] | None = None,
) -> list[AgUiApprovalDecision]:
    if not ledger or not interrupt_owners:
        return []
    needs = {call.public_id for row in ledger for call in continuations.get(row.resumed_run_id, ())}
    issued = _visible_issuers(merged, public_run_ids, needs)
    children = {row["subagentRunId"] for row in merged.subagents}
    identified: dict[str, list[AgUiApprovalDecision]] = defaultdict(list)
    for item in ledger:
        continuation = runs.get(item.resumed_run_id)
        if (
            continuation is None
            or continuation.source != "resume"
            or continuation.parent_run_id != item.parent_run_id
            or item.parent_run_id not in runs
        ):
            continue
        candidates: list[_Issuer] = []
        for admission in continuations.get(item.resumed_run_id, ()):
            issuer = issued.get(admission.public_id)
            if (
                issuer is not None
                and issuer.physical in runs
                and issuer.physical in item.source_run_ids
                and issuer.owner == admission.owner
                and issuer.name == admission.name
                and public_tool_call_id(
                    public_run_ids[issuer.physical],
                    issuer.owner,
                    admission.parent_message_id,
                    admission.raw_id,
                )
                == issuer.call_id
                and (admission.parent_message_id or admission.public_id) == issuer.message_id
                and _owns_approval(
                    item,
                    issuer,
                    thread=thread_id,
                    children=children,
                    visibility=visibility,
                    owners=interrupt_owners,
                )
            ):
                candidates.append(issuer)
        for decision in _match_batch(item, candidates):
            identified[decision.tool_call_id].append(decision)
    return [rows[0] for rows in identified.values() if all(row == rows[0] for row in rows)]


async def snapshot_approval_decisions(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    history: SnapshotHistory,
    merged: AssembledSnapshot,
    runs: Mapping[UUID, ConversationRun],
    public_run_ids: Mapping[UUID, str],
    visibility: Mapping[UUID, ChildVisibility],
    continuations: Mapping[UUID, tuple[ToolContinuation, ...]],
    interrupt_owners: Mapping[UUID, Mapping[str, str | None]] | None = None,
) -> list[AgUiApprovalDecision]:
    if not interrupt_owners or not continuations:
        return []
    branch_runs = {
        ident: run for ident, run in runs.items() if ident not in history.other_branch_run_ids
    }
    if not any(
        calls and ident in branch_runs and branch_runs[ident].source == "resume"
        for ident, calls in continuations.items()
    ):
        return []
    # runs is the snapshot's selected, fully proved branch. Its final scope and
    # visibility are rechecked by the caller before returning this projection.
    ledger = await load_consumed_approval_decisions(
        db,
        conversation=conversation,
        context=context,
        branch_run_ids=branch_runs.keys(),
    )
    return match_approval_decisions(
        ledger,
        thread_id=str(conversation.id),
        merged=merged,
        runs=branch_runs,
        public_run_ids=public_run_ids,
        visibility=visibility,
        continuations=continuations,
        interrupt_owners=interrupt_owners,
    )
