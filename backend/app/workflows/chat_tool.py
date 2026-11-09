"""Published workflows as chat tools (workflow-tool-plan).

An agent's ``agent_workflows`` links become tools of its **root chat run** only (T4, T7): no
subagent, workflow step, schedule or API run gets them, so workflows never call workflows.
A tool is built from the workflow's current published version (T3): its inputs are the
tool's arguments, its final output (or why it failed) the tool's result. Only the person
chatting may run it (owners only, T6); otherwise the link reads as broken.

Calling the tool starts a run tied to this chat (``parent_conversation_id`` +
``parent_tool_call_id``, unique together) and pauses the chat with a ``workflow_wait``
interrupt. LangGraph runs a resumed tool call again from the top, so the call finds its run
by that pair instead of starting another, and returns the run's real result once it has
ended. The resume value is never read: whoever resumes, the tool reports the run as it is.
``chat_resume.resume_finished_chats`` (the API scheduler) resumes a chat once its run ends.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Final

from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from pydantic import BaseModel, Field, create_model
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.scoping import private_scoped
from app.exceptions import AppError, ValidationError
from app.models.agent_workflow import AgentWorkflowLink
from app.models.workflow import RUN_TERMINAL, Workflow, WorkflowRun, WorkflowVersion
from app.workflows.definition import WorkflowDefinition, WorkflowInput

TOOL_PREFIX: Final = "workflow_"
WAIT_TYPE: Final = "workflow_wait"
MAX_LINKS: Final = 20
_TYPES: Final[dict[str, type]] = {"string": str, "number": float, "integer": int, "boolean": bool}


def tool_name(workflow_id: uuid.UUID | str) -> str:
    """ASCII, stable across renames (the model sees the workflow's name in the description)."""
    return f"{TOOL_PREFIX}{uuid.UUID(str(workflow_id)).hex[:12]}"


@dataclass(frozen=True)
class LinkedWorkflow:
    workflow_id: uuid.UUID
    name: str
    description: str | None
    published_version_no: int | None
    usable: bool
    broken_reason: str | None


async def _published(db: AsyncSession, workflow: Workflow) -> WorkflowDefinition | None:
    if workflow.archived_at is not None or workflow.published_version_no is None:
        return None
    version = await db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version_no == workflow.published_version_no,
        )
    )
    return None if version is None else WorkflowDefinition.model_validate(version.definition)


def _broken_reason(workflow: Workflow | None) -> str | None:
    if workflow is None:
        return "workflow_unavailable"  # deleted, or not the person's (owners only, T6)
    if workflow.archived_at is not None:
        return "workflow_archived"
    if workflow.published_version_no is None:
        return "workflow_not_published"
    return None


async def list_links(
    db: AsyncSession, context: OrgContext, agent_id: uuid.UUID
) -> list[LinkedWorkflow]:
    """The agent's workflow tools as the person sees them (usable, or why not)."""
    links = (
        await db.execute(
            select(AgentWorkflowLink)
            .where(AgentWorkflowLink.agent_id == agent_id)
            .order_by(AgentWorkflowLink.position)
        )
    ).scalars()
    rows = []
    for link in links:
        workflow = await db.scalar(
            private_scoped(
                select(Workflow).where(Workflow.id == link.workflow_id), Workflow, context
            )
        )
        reason = _broken_reason(workflow)
        rows.append(
            LinkedWorkflow(
                workflow_id=link.workflow_id,
                name=workflow.name if workflow else "",
                description=workflow.description if workflow else None,
                published_version_no=workflow.published_version_no if workflow else None,
                usable=reason is None,
                broken_reason=reason,
            )
        )
    return rows


async def set_links(
    db: AsyncSession,
    context: OrgContext,
    agent_id: uuid.UUID,
    workflow_ids: list[uuid.UUID],
) -> list[LinkedWorkflow]:
    """Replace the agent's workflow tools. Each must be the person's published workflow."""
    unique = list(dict.fromkeys(workflow_ids))
    if len(unique) > MAX_LINKS:
        raise ValidationError("WORKFLOW_TOOLS_TOO_MANY", f"워크플로 도구는 {MAX_LINKS}개까지입니다")
    for workflow_id in unique:
        workflow = await db.scalar(
            private_scoped(select(Workflow).where(Workflow.id == workflow_id), Workflow, context)
        )
        reason = _broken_reason(workflow)
        if reason is not None:
            raise AppError(
                "WORKFLOW_TOOL_UNAVAILABLE",
                "게시된 내 워크플로만 도구로 붙일 수 있습니다",
                status=422,
                details={"workflow_id": str(workflow_id), "reason": reason},
            )
    await db.execute(delete(AgentWorkflowLink).where(AgentWorkflowLink.agent_id == agent_id))
    db.add_all(
        AgentWorkflowLink(
            agent_id=agent_id,
            workflow_id=workflow_id,
            position=position,
            delegated_by=context.user_id,
        )
        for position, workflow_id in enumerate(unique)
    )
    await db.flush()
    return await list_links(db, context, agent_id)


def args_model(workflow_id: uuid.UUID, inputs: list[WorkflowInput]) -> type[BaseModel]:
    """The tool's arguments: the published version's inputs, with their types and limits,
    plus the injected tool call id (hidden from the model)."""
    fields: dict[str, Any] = {"tool_call_id": (Annotated[str, InjectedToolCallId], ...)}
    for item in inputs:
        if item.type == "file":
            # A chat model has no upload to give: a required file input makes the run
            # refuse to start, and the tool answers why (workflow-nodes-plan N8).
            continue
        kind = _TYPES[item.type]
        constraints: dict[str, Any] = {"description": item.label or item.name}
        if item.max_length is not None:
            constraints["max_length"] = item.max_length
        if item.required:
            fields[item.name] = (kind, Field(..., **constraints))
        else:
            fields[item.name] = (kind | None, Field(None, **constraints))
    return create_model(f"WorkflowArgs_{uuid.UUID(str(workflow_id)).hex[:8]}", **fields)


def run_result(run: WorkflowRun) -> dict[str, Any]:
    """What the model reads back: the output, or why the run did not complete."""
    if run.status == "completed":
        return {"status": "completed", "output": run.output}
    if run.status in RUN_TERMINAL:
        return {"status": run.status, "error": run.error_message or run.error_code}
    return {
        "status": run.status,
        "note": "아직 끝나지 않았습니다. 결과는 워크플로 실행 상세에서 확인할 수 있습니다.",
    }


async def _find_or_start(
    db: AsyncSession,
    context: OrgContext,
    workflow_id: uuid.UUID,
    inputs: dict[str, Any],
    *,
    conversation_id: uuid.UUID,
    tool_call_id: str,
    conversation_run_id: uuid.UUID | None,
) -> WorkflowRun:
    from app.services import audit_service
    from app.workflows import runner

    existing = await db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.parent_conversation_id == conversation_id,
            WorkflowRun.parent_tool_call_id == tool_call_id,
        )
    )
    if existing is not None:
        return existing
    run = await runner.start_run(
        db,
        context,
        workflow_id,
        inputs,
        parent={
            "parent_conversation_id": conversation_id,
            "parent_tool_call_id": tool_call_id,
            "parent_conversation_run_id": conversation_run_id,
        },
    )
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=context.user_id,
        owner_user_id=context.user_id,
        action=runner.RUN_STARTED,
        target_type="workflow_run",
        target_id=run.id,
        target_owner_user_id=context.user_id,
        outcome="success",
        metadata={
            "workflow_id": str(workflow_id),
            "version_id": str(run.version_id),
            "started_from": "chat_tool",
            "parent_conversation_id": str(conversation_id),
            "parent_conversation_run_id": str(conversation_run_id) if conversation_run_id else None,
        },
    )
    await db.commit()
    return run


@dataclass(frozen=True)
class WorkflowToolSpec:
    workflow_id: uuid.UUID
    name: str
    description: str | None
    inputs: list[WorkflowInput]


async def specs_for_agent(
    db: AsyncSession, context: OrgContext, agent_id: uuid.UUID
) -> list[WorkflowToolSpec]:
    """The workflows the person chatting may call: theirs, published, not archived."""
    specs: list[WorkflowToolSpec] = []
    links = (
        await db.execute(
            select(AgentWorkflowLink)
            .where(AgentWorkflowLink.agent_id == agent_id)
            .order_by(AgentWorkflowLink.position)
        )
    ).scalars()
    for link in links:
        workflow = await db.scalar(
            private_scoped(
                select(Workflow).where(Workflow.id == link.workflow_id), Workflow, context
            )
        )
        definition = await _published(db, workflow) if workflow is not None else None
        if workflow is None or definition is None:
            continue
        specs.append(
            WorkflowToolSpec(workflow.id, workflow.name, workflow.description, definition.inputs)
        )
    return specs


def build_tool(
    spec: WorkflowToolSpec,
    context: OrgContext,
    *,
    conversation_id: uuid.UUID,
    conversation_run_id: uuid.UUID | None,
) -> BaseTool:
    """One workflow tool for this chat run (see the module docstring for the wait)."""
    from langgraph.types import interrupt

    from app.database import async_session, tenant_session

    model = args_model(spec.workflow_id, spec.inputs)

    async def call(tool_call_id: Annotated[str, InjectedToolCallId], **inputs: Any) -> str:
        values = {name: value for name, value in inputs.items() if value is not None}
        async with tenant_session(context, maker=async_session) as db:
            try:
                run = await _find_or_start(
                    db,
                    context,
                    spec.workflow_id,
                    values,
                    conversation_id=conversation_id,
                    tool_call_id=tool_call_id,
                    conversation_run_id=conversation_run_id,
                )
            except AppError as exc:  # unpublished, bad inputs, changed agents: no wait
                return json.dumps(
                    {"status": "failed", "error": exc.message, "code": exc.code},
                    ensure_ascii=False,
                )
            run_id, status = run.id, run.status
            if status in RUN_TERMINAL:
                return json.dumps(run_result(run), ensure_ascii=False)
        interrupt(
            {
                "type": WAIT_TYPE,
                "workflow_run_id": str(run_id),
                "workflow_id": str(spec.workflow_id),
                "workflow_name": spec.name,
            }
        )
        # Resumed before the run ended (someone answered the wait by hand): report as it is.
        async with tenant_session(context, maker=async_session) as db:
            current = await db.get(WorkflowRun, run_id)
            result = run_result(current) if current else {"status": "gone"}
        return json.dumps(result, ensure_ascii=False)

    description = (
        f"워크플로 '{spec.name}'을(를) 한 번 실행하고 최종 출력을 돌려받습니다. "
        "승인을 기다리느라 오래 걸릴 수 있으며, 끝나면 결과로 대화가 이어집니다."
    )
    if spec.description:
        description += f"\n\n워크플로 설명: {spec.description}"
    return StructuredTool.from_function(
        coroutine=call,
        name=tool_name(spec.workflow_id),
        description=description,
        args_schema=model,
    )


def workflow_tools_for_run(cfg: Any, run_id: str | None) -> list[BaseTool]:
    """The root chat run's workflow tools from ``cfg.workflow_tools`` (read when the chat
    config was built); empty for any other run path (see the module docstring)."""
    specs: list[WorkflowToolSpec] = list(getattr(cfg, "workflow_tools", None) or [])
    if not specs or cfg.run_path != "chat":
        return []
    conversation_id = uuid.UUID(str(cfg.thread_id))
    conversation_run_id = uuid.UUID(run_id) if run_id else None
    return [
        build_tool(
            spec,
            cfg.org_context,
            conversation_id=conversation_id,
            conversation_run_id=conversation_run_id,
        )
        for spec in specs
    ]


__all__ = [
    "MAX_LINKS",
    "TOOL_PREFIX",
    "WAIT_TYPE",
    "LinkedWorkflow",
    "WorkflowToolSpec",
    "args_model",
    "build_tool",
    "list_links",
    "run_result",
    "set_links",
    "specs_for_agent",
    "tool_name",
    "workflow_tools_for_run",
]
