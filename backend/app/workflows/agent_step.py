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

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.agent_invoke_runner import execute_agent_invoke_structured
from app.approvals.emergency import STOPPED_CODE
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
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
from app.workflows.definition import AgentNode

logger = logging.getLogger(__name__)

_FINISHED_RUN: Final = frozenset({"completed", "failed", "canceled"})


@dataclass(frozen=True, slots=True)
class StepOutcome:
    status: str  # completed | failed | unknown | canceled
    output: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedStep:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    config: Any
    prompt: str


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
    node: AgentNode,
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
        # W2a: a tool call that needs approval answers BLOCKED; waits arrive with W2b.
        cfg.unattended_approval = "block"
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
    except AppError as exc:
        code = "emergency_stop" if exc.code == STOPPED_CODE else exc.code.lower()
        return StepOutcome("failed", error_code=code[:80], error_message=exc.message)
    return PreparedStep(accepted.run_id, conversation.id, cfg, prompt)


async def execute(
    context: OrgContext, step: WorkflowStepRun, prepared: PreparedStep
) -> StepOutcome:
    """Run the accepted step under durable ownership; the result is checked, not trusted."""
    ownership = RuntimeOwnershipRequest(
        prepared.run_id, prepared.conversation_id, uuid.UUID(str(step.agent_id)), context
    )

    async def operation() -> Any:
        return await execute_agent_invoke_structured(
            prepared.config,
            [{"role": "user", "content": prepared.prompt}],
            run_id=str(prepared.run_id),
            moldy_source="workflow",
        )

    try:
        result = await run_owned_execution(ownership, operation)
    except AppError as exc:
        return StepOutcome("failed", error_code=exc.code.lower()[:80], error_message=exc.message)
    except Exception:
        logger.exception("workflow agent step %s failed", step.id)
        return StepOutcome("failed", error_code="agent_failed", error_message="에이전트 실행 실패")
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


__all__ = ["PreparedStep", "StepOutcome", "execute", "prepare", "recover"]
