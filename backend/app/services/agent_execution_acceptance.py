"""Owned Agent execution acceptance extracted from ``agent_execution_owner`` (Task 10 I4).

Envelope validation, run creation, participant rows, and envelope binding, moved
unchanged; ``agent_execution_owner`` re-exports every name.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, noload

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.execution_lock_cursor import LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof, lock_row
from app.authz.organization_execution_proof import ExecutionTransactionProof
from app.models.agent_api import AgentApiRun
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.agent_lifecycle_locks import AgentTreeSnapshot
from app.services.conversation_run_creation import create_run_from_locked_proof
from app.services.conversation_run_envelope_projection import (
    api_status_for_run,
    trigger_status_for_run,
)
from app.services.conversation_run_service import RunSource

type OwnedExecutionSource = Literal["chat", "api", "trigger"]
type EnvelopeKind = Literal["api", "trigger"]


class ExecutionOwnershipError(ValueError):
    """The requested business envelope cannot own the accepted execution."""


@dataclass(frozen=True, slots=True)
class ExecutionEnvelopeRef:
    kind: EnvelopeKind
    envelope_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class OwnedExecutionRequest:
    conversation_id: uuid.UUID
    root_agent_id: uuid.UUID
    context: OrgContext
    source: OwnedExecutionSource
    input_preview: str | None
    envelope: ExecutionEnvelopeRef | None = None
    run_source: RunSource | None = None
    parent_run_id: uuid.UUID | None = None
    interrupt_id: str | None = None
    run_metadata: dict[str, JsonValue] | None = None
    allow_legacy_resume: bool = False


@dataclass(frozen=True, slots=True)
class AcceptedParticipant:
    agent_id_snapshot: uuid.UUID
    role: Literal["root", "subagent"]
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OwnedExecutionAcceptance:
    run_id: uuid.UUID
    participants: tuple[AcceptedParticipant, ...]
    envelope: ExecutionEnvelopeRef | None


def _participant_paths(snapshot: AgentTreeSnapshot) -> Mapping[uuid.UUID, tuple[str, ...]]:
    paths: dict[uuid.UUID, tuple[str, ...]] = {snapshot.root_agent_id: ()}
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for edge in snapshot.edges:
        children.setdefault(edge.parent_agent_id, []).append(edge.child_agent_id)
    pending = deque([snapshot.root_agent_id])
    while pending:
        parent_id = pending.popleft()
        for child_id in sorted(children.get(parent_id, ()), key=lambda item: item.bytes):
            parent_path = paths[parent_id]
            candidate = (
                (str(parent_id), str(child_id))
                if not parent_path
                else (*parent_path, str(child_id))
            )
            if child_id not in paths or candidate < paths[child_id]:
                paths[child_id] = candidate
                pending.append(child_id)
    return paths


async def _validated_envelope(
    db: AsyncSession,
    request: OwnedExecutionRequest,
    proof: ExecutionTransactionProof,
) -> AgentApiRun | AgentTriggerRun | None:
    envelope = request.envelope
    if request.source == "chat":
        if envelope is not None:
            raise ExecutionOwnershipError("Chat execution cannot bind a business envelope")
        return None
    if envelope is None or envelope.kind != request.source:
        raise ExecutionOwnershipError("External execution requires its matching business envelope")
    if request.source == "api":
        row: AgentApiRun | AgentTriggerRun | None = await lock_row(
            db,
            proof.cursor,
            LockClass.ENVELOPE,
            envelope.envelope_id,
            select(AgentApiRun)
            .options(
                load_only(
                    AgentApiRun.id,
                    AgentApiRun.user_id,
                    AgentApiRun.conversation_id,
                    AgentApiRun.conversation_run_id,
                    AgentApiRun.status,
                    raiseload=True,
                ),
                noload(AgentApiRun.deployment),
                noload(AgentApiRun.thread),
            )
            .where(AgentApiRun.id == envelope.envelope_id)
            .with_for_update(),
        )
    else:
        row = await lock_row(
            db,
            proof.cursor,
            LockClass.ENVELOPE,
            envelope.envelope_id,
            select(AgentTriggerRun)
            .options(
                load_only(
                    AgentTriggerRun.id,
                    AgentTriggerRun.agent_id,
                    AgentTriggerRun.user_id,
                    AgentTriggerRun.conversation_id,
                    AgentTriggerRun.conversation_run_id,
                    AgentTriggerRun.status,
                    raiseload=True,
                ),
                noload(AgentTriggerRun.trigger),
                noload(AgentTriggerRun.conversation),
            )
            .where(AgentTriggerRun.id == envelope.envelope_id)
            .with_for_update(),
        )
    if (
        row is None
        or row.conversation_id != request.conversation_id
        or row.user_id != request.context.user_id
    ):
        raise ExecutionOwnershipError(
            "Execution envelope does not belong to the accepted conversation"
        )
    if row.conversation_run_id is not None:
        raise ExecutionOwnershipError("Execution envelope already has a durable owner")
    conversation_matches = await db.scalar(
        select(Conversation.id).where(
            Conversation.id == row.conversation_id,
            Conversation.agent_id == request.root_agent_id,
            Conversation.user_id == request.context.user_id,
            Conversation.org_id == request.context.org_id,
            Conversation.tenant_id == request.context.tenant_id,
        )
    )
    if conversation_matches is None:
        raise ExecutionOwnershipError(
            "Execution envelope provenance does not match the accepted Agent"
        )
    if isinstance(row, AgentTriggerRun) and row.agent_id != request.root_agent_id:
        raise ExecutionOwnershipError(
            "Trigger envelope provenance does not match the accepted Agent"
        )
    return row


def _bind_envelope(
    row: AgentApiRun | AgentTriggerRun,
    envelope: ExecutionEnvelopeRef,
    run: ConversationRun,
) -> None:
    row.conversation_run_id = run.id
    row.status = (
        api_status_for_run(run.status)
        if envelope.kind == "api"
        else trigger_status_for_run(run.status)
    )


async def accept_owned_execution(
    db: AsyncSession,
    request: OwnedExecutionRequest,
) -> OwnedExecutionAcceptance:
    """Accept one run, its full tree, and its business link under lifecycle locks.

    The canonical execution proof (parent/member/User prefix, Agent tree,
    Conversation) is obtained before the envelope is validated or the run is
    created; every later lock follows the proof's cursor.
    """
    proof = await acquire_execution_proof(
        db,
        request.context,
        root_agent_id=request.root_agent_id,
        conversation_ids=(request.conversation_id,),
    )
    executable = await load_agent_tree_for_execution(
        db, AgentExecutionTarget(request.root_agent_id, request.context, run=True)
    )
    envelope = await _validated_envelope(db, request, proof)
    run = await create_run_from_locked_proof(
        db,
        proof,
        conversation_id=request.conversation_id,
        agent_id=request.root_agent_id,
        context=request.context,
        source=request.run_source or ("chat" if request.source == "chat" else "start"),
        input_preview=request.input_preview,
        parent_run_id=request.parent_run_id,
        interrupt_id=request.interrupt_id,
        metadata=request.run_metadata,
        allow_legacy_resume=request.allow_legacy_resume,
    )
    paths = _participant_paths(executable.snapshot)
    participants = tuple(
        AcceptedParticipant(
            member.agent_id,
            "root" if member.agent_id == request.root_agent_id else "subagent",
            paths[member.agent_id],
        )
        for member in executable.snapshot.members
    )
    db.add_all(
        [
            ConversationRunAgent(
                run_id=run.id,
                agent_id=item.agent_id_snapshot,
                agent_id_snapshot=item.agent_id_snapshot,
                role=item.role,
                path=list(item.path),
            )
            for item in participants
        ]
    )
    if envelope is not None and request.envelope is not None:
        _bind_envelope(envelope, request.envelope, run)
    await db.flush()
    return OwnedExecutionAcceptance(run.id, participants, request.envelope)
