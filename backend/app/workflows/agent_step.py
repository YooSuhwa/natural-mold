"""One agent node of a workflow run (workflow-plan 3.4-3.5, W2a).

The step runs the agent the way a schedule does, never through the chat HTTP routes:
an internal conversation (``source="workflow"``, hidden from every list), one accepted
``conversation_runs`` row bound to the step row (the envelope), durable ownership, and
an invoke with the node's output schema. The runner is the caller: a ``per_user`` agent
uses the runner's own connections; ``fixed`` agents run as their owner.

A step whose run was accepted but whose result was never stored is **not run again**
(an agent may have sent mail): it becomes ``unknown`` for a person to look at.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.agent_invoke_runner import execute_agent_invoke_structured
from app.approvals import recording
from app.approvals.emergency import STOPPED_CODE
from app.approvals.service import RunOrigin
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_attachment import MessageAttachment
from app.models.workflow import WorkflowStepRun
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    OwnedExecutionRequest,
    accept_owned_execution,
)
from app.services.agent_execution_runtime import RuntimeOwnershipRequest, run_owned_execution
from app.services.agent_invocation_service import (
    AgentInvocationPrincipal,
    build_agent_config_for_loaded_agent,
)
from app.services.conversation_runtime_policy import (
    ensure_conversation_runtime_policy,
    snapshot_from_conversation,
)
from app.storage.paths import ForeignTenantPathError, upload_file_path

logger = logging.getLogger(__name__)

_FINISHED_RUN: Final = frozenset({"completed", "failed", "canceled"})


@dataclass(frozen=True, slots=True)
class StepOutcome:
    status: str  # completed | failed | unknown | canceled | waiting
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedStep:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    config: Any
    prompt: str
    # The input messages (the human message carries the attached files' references).
    messages: list[Any] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StepAgent:
    """What one agent execution of a step runs: an agent node, or a child row of a
    ``for_each``/``parallel`` step (workflow-nodes-plan 3.1)."""

    agent_id: str
    output_schema: dict[str, Any]
    # ``wait``: a tool that needs approval waits in the inbox (agent nodes, W2c). Child rows
    # use ``block`` (the call is refused): their parent has no single resume point.
    unattended: Literal["wait", "block"] = "wait"
    # ``file`` inputs the prompt references: ``[{"id", "filename"}]`` (N8).
    files: tuple[dict[str, str], ...] = ()


def record(step: WorkflowStepRun, outcome: StepOutcome) -> None:
    """Store an outcome on its step row."""
    from datetime import UTC, datetime

    step.status = outcome.status
    step.output = outcome.output
    step.error_code = outcome.error_code
    step.error_message = outcome.error_message
    step.finished_at = datetime.now(UTC).replace(tzinfo=None)


async def recover(db: AsyncSession, step: WorkflowStepRun) -> StepOutcome | None:
    """For a step that already has an accepted run: what happened, without running again.

    ``None`` only for a step that was never accepted (safe to accept now).
    """
    if step.conversation_run_id is None:
        return None
    status = await db.scalar(
        select(ConversationRun.status).where(ConversationRun.id == step.conversation_run_id)
    )
    if status == "canceled":
        return StepOutcome("canceled", error_code="canceled")
    if status == "failed":
        return StepOutcome("failed", error_code="agent_failed", error_message="에이전트 실행 실패")
    # Running, stale, or finished without a stored result: nobody can tell what the agent did.
    return StepOutcome(
        "unknown",
        error_code="step_outcome_unknown",
        error_message="실행 도중 멈춰 결과를 알 수 없습니다. 확인 후 다시 실행하세요",
    )


async def prepare(
    db: AsyncSession,
    context: OrgContext,
    step: WorkflowStepRun,
    node: StepAgent,
    *,
    prompt: str,
    title: str,
) -> PreparedStep | StepOutcome:
    """Conversation, config and acceptance in the caller's transaction (committed by it)."""
    agent_id = uuid.UUID(node.agent_id)
    try:
        if step.conversation_id is None:
            conversation = await chat_service.create_conversation(
                db, agent_id, title[:200], context=context, source="workflow"
            )
            await ensure_conversation_runtime_policy(
                db, ConversationPolicyTarget(conversation.id, context)
            )
            step.conversation_id = conversation.id
            await db.flush()
        conversation = await db.get(Conversation, step.conversation_id)
        if conversation is None:
            return StepOutcome("failed", error_code="conversation_gone")
        agent = await chat_service.get_agent_with_tools(db, agent_id, context)
        if agent is None:
            return StepOutcome("failed", error_code="agent_unavailable")
        runtime_policy = snapshot_from_conversation(conversation)
        if runtime_policy is None:
            return StepOutcome("failed", error_code="runtime_policy_missing")
        cfg = await build_agent_config_for_loaded_agent(
            db,
            agent,
            context=context,
            thread_id=str(conversation.id),
            principal=AgentInvocationPrincipal.workflow_runner(context.user_id),
            source="workflow",
            runtime_policy=runtime_policy,
        )
        cfg.output_schema = node.output_schema
        # A tool call that needs approval waits for the runner in the inbox (W2c).
        cfg.unattended_approval = node.unattended
        attachment_ids = await _attach_files(db, context, step, conversation.id, node.files)
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=conversation.id,
                root_agent_id=agent_id,
                context=context,
                source="workflow",
                input_preview=prompt[:500],
                envelope=ExecutionEnvelopeRef("workflow", step.id),
            ),
        )
        messages: list[Any] = [{"role": "user", "content": prompt}]
        if attachment_ids:
            from app.services.chat.attachment_inputs import prepare_attachment_inputs

            messages, inputs = await prepare_attachment_inputs(
                db,
                run_id=accepted.run_id,
                conversation_id=conversation.id,
                context=context,
                model_id=str(agent.model_id) if agent.model_id else None,
                attachment_ids=attachment_ids,
                input_payload=messages,
                tag_input=True,
            )
            cfg.attachment_inputs = inputs
    except AppError as exc:
        code = "emergency_stop" if exc.code == STOPPED_CODE else exc.code.lower()
        return StepOutcome("failed", error_code=code[:80], error_message=exc.message)
    except FileNotFoundError:
        return StepOutcome(
            "failed", error_code="file_gone", error_message="입력 파일을 찾을 수 없습니다"
        )
    return PreparedStep(accepted.run_id, conversation.id, cfg, prompt, messages)


async def _attach_files(
    db: AsyncSession,
    context: OrgContext,
    step: WorkflowStepRun,
    conversation_id: uuid.UUID,
    files: tuple[dict[str, str], ...],
) -> list[uuid.UUID]:
    """Copy each input file into the step's conversation as its own attachment.

    A copy, not a shared row: an upload belongs to one conversation and deleting that
    conversation deletes its blob, so steps never share one. ``message_id`` marks the
    copy as sent (the never-sent upload sweep skips it).
    """
    ids: list[uuid.UUID] = []
    for item in files:
        source = await db.get(MessageAttachment, uuid.UUID(item["id"]))
        if source is None or source.user_id != context.user_id:
            raise FileNotFoundError(item["id"])
        try:
            origin = upload_file_path(source.storage_path, source.tenant_id)
        except ForeignTenantPathError as exc:
            raise FileNotFoundError(item["id"]) from exc
        copy_id = uuid.uuid4()
        target = origin.with_name(f"{copy_id}{origin.suffix}")
        await asyncio.to_thread(_copy_file, origin, target)
        db.add(
            MessageAttachment(
                id=copy_id,
                user_id=source.user_id,
                org_id=source.org_id,
                tenant_id=source.tenant_id,
                conversation_id=conversation_id,
                message_id=f"workflow-step:{step.id}",
                filename=source.filename,
                mime_type=source.mime_type,
                size_bytes=source.size_bytes,
                storage_path=str(target),
                url=f"/api/uploads/{copy_id}",
            )
        )
        ids.append(copy_id)
    if ids:
        await db.flush()
    return ids


def _copy_file(origin: Path, target: Path) -> None:
    if not origin.is_file():
        raise FileNotFoundError(str(origin))
    shutil.copyfile(origin, target)


async def execute(
    context: OrgContext, step: WorkflowStepRun, prepared: PreparedStep
) -> StepOutcome:
    """Run the accepted step under durable ownership; the result is checked, not trusted."""
    ownership = RuntimeOwnershipRequest(
        prepared.run_id, prepared.conversation_id, uuid.UUID(str(step.agent_id)), context
    )

    async def operation() -> Any:
        result = await execute_agent_invoke_structured(
            prepared.config,
            prepared.messages or [{"role": "user", "content": prepared.prompt}],
            run_id=str(prepared.run_id),
            moldy_source="workflow",
        )
        # The graph paused before a tool that needs a person: the step waits (W2c).
        waiting = await recording.waiting_interrupts(prepared.conversation_id)
        if waiting and await recording.pause(
            run_id=prepared.run_id,
            context=context,
            interrupts=waiting,
            origin=RunOrigin(
                source="workflow", workflow_run_id=step.run_id, workflow_step_run_id=step.id
            ),
            secret_values=prepared.config.secret_values,
        ):
            return None
        return result

    try:
        result = await run_owned_execution(ownership, operation)
    except AppError as exc:
        return StepOutcome("failed", error_code=exc.code.lower()[:80], error_message=exc.message)
    except Exception:
        logger.exception("workflow agent step %s failed", step.id)
        return StepOutcome("failed", error_code="agent_failed", error_message="에이전트 실행 실패")
    if result is None:
        return StepOutcome("waiting")
    outcome = result.outcome
    if outcome.status == "ok":
        return StepOutcome("completed", output=outcome.value)
    messages = {
        "schema_invalid": "에이전트 답이 출력 형식에 맞지 않습니다",
        "refused": "에이전트가 답을 거부했습니다",
        "truncated": "에이전트 답이 길이 제한에서 잘렸습니다",
    }
    return StepOutcome(
        "failed", error_code=f"output_{outcome.status}", error_message=messages[outcome.status]
    )


__all__ = [
    "PreparedStep",
    "StepAgent",
    "StepOutcome",
    "execute",
    "prepare",
    "record",
    "recover",
]
