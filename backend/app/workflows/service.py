"""Workflow drafts, checks and publishing (workflow-plan 3.1-3.3, 3.6, W1).

Owner only in 1차 (Q5): every query is ``private_scoped`` to the caller and organization,
so another person's workflow is a plain 404. A draft save names the revision it edited;
a stale one is a 409 and nothing is written. Publishing re-checks the draft it was given
(the same revision), pins each agent node's settings fingerprint (Q1) and freezes an
immutable version.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import current_authz, require_agent_permission
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate, private_scoped
from app.exceptions import AppError, ConflictError, NotFoundError
from app.models.agent import Agent
from app.models.model import Model
from app.models.workflow import Workflow, WorkflowVersion
from app.workflows.checks import Issue, graph_issues
from app.workflows.definition import AgentNode, WorkflowDefinition, empty_definition
from app.workflows.pins import agent_fingerprints

STANDARD_PROFILE: Final = "standard"


def workflow_not_found() -> NotFoundError:
    return NotFoundError("WORKFLOW_NOT_FOUND", "워크플로를 찾을 수 없습니다")


def workflow_version_not_found() -> NotFoundError:
    return NotFoundError("WORKFLOW_VERSION_NOT_FOUND", "워크플로 버전을 찾을 수 없습니다")


def _stale(current: int) -> ConflictError:
    error = ConflictError("WORKFLOW_DRAFT_STALE", "다른 곳에서 먼저 저장했습니다. 새로 불러오세요")
    error.details = {"draft_revision": current}
    return error


def _archived() -> ConflictError:
    return ConflictError("WORKFLOW_ARCHIVED", "보관한 워크플로는 고칠 수 없습니다")


@dataclass(frozen=True, slots=True)
class PublishResult:
    workflow: Workflow
    version: WorkflowVersion


def parse_definition(raw: Any) -> WorkflowDefinition:
    """The draft's shape, or a 422 listing the first problems (never a 500)."""
    try:
        return WorkflowDefinition.model_validate(raw)
    except PydanticValidationError as exc:
        problems = [
            {"loc": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors(include_url=False, include_input=False)[:20]
        ]
        raise AppError(
            "WORKFLOW_DEFINITION_INVALID",
            "워크플로 정의 형식이 올바르지 않습니다",
            status=422,
            details={"problems": problems},
        ) from exc


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def create_workflow(
    db: AsyncSession, context: OrgContext, *, name: str, description: str | None
) -> Workflow:
    workflow = Workflow(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name=name,
        description=description,
        draft_definition=empty_definition(),
        draft_revision=1,
    )
    db.add(workflow)
    await db.flush()
    return workflow


async def list_workflows(
    db: AsyncSession, context: OrgContext, *, include_archived: bool = False
) -> list[Workflow]:
    stmt = private_scoped(select(Workflow), Workflow, context)
    if not include_archived:
        stmt = stmt.where(Workflow.archived_at.is_(None))
    stmt = stmt.order_by(Workflow.updated_at.desc(), Workflow.id)
    return list((await db.execute(stmt)).scalars().all())


async def get_workflow(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID, *, for_update: bool = False
) -> Workflow:
    stmt = private_scoped(select(Workflow).where(Workflow.id == workflow_id), Workflow, context)
    if for_update:
        stmt = stmt.with_for_update()
    workflow = (await db.execute(stmt)).scalar_one_or_none()
    if workflow is None:
        raise workflow_not_found()
    return workflow


async def save_draft(
    db: AsyncSession,
    context: OrgContext,
    workflow_id: uuid.UUID,
    *,
    revision: int,
    definition: Any | None = None,
    name: str | None = None,
    description: str | None = None,
) -> Workflow:
    """Write the draft the caller edited from ``revision``; a newer draft wins (409)."""
    workflow = await get_workflow(db, context, workflow_id, for_update=True)
    if workflow.archived_at is not None:
        raise _archived()
    if workflow.draft_revision != revision:
        raise _stale(workflow.draft_revision)
    if definition is not None:
        workflow.draft_definition = parse_definition(definition).dump()
    if name is not None:
        workflow.name = name
    if description is not None:
        workflow.description = description
    workflow.draft_revision = revision + 1
    workflow.updated_at = _now()
    await db.flush()
    return workflow


async def check_workflow(db: AsyncSession, context: OrgContext, workflow: Workflow) -> list[Issue]:
    """Everything that would stop the current draft from publishing."""
    definition = parse_definition(workflow.draft_definition)
    return graph_issues(definition) + await agent_issues(db, context, definition)


async def agent_issues(
    db: AsyncSession, context: OrgContext, definition: WorkflowDefinition
) -> list[Issue]:
    """Agent nodes the runner could not use: missing, hidden, archived, not runnable,
    or on a model that cannot call tools (structured output needs a tool call)."""
    nodes = [node for node in definition.nodes if isinstance(node, AgentNode)]
    ids = {node.id: uuid.UUID(node.agent_id) for node in nodes}
    rows = {
        row.id: row
        for row in (
            await db.execute(
                select(
                    Agent.id,
                    Agent.org_id,
                    Agent.archived_at,
                    Agent.runtime_profile,
                    Model.supports_function_calling,
                    private_scope_predicate(Agent, context).label("owned"),
                )
                .join(Model, Model.id == Agent.model_id)
                .where(Agent.id.in_(set(ids.values())))
                .execution_options(autoflush=False)
            )
        ).all()
    }
    issues: list[Issue] = []
    for node in nodes:
        row = rows.get(ids[node.id])
        # Hidden runtimes (skill builder etc.) read as missing, never as "hidden".
        if (
            row is None
            or row.runtime_profile != STANDARD_PROFILE
            or not await _can_run(context, row)
        ):
            issues.append(Issue("agent_missing", "에이전트를 찾을 수 없습니다", node.id))
        elif row.archived_at is not None:
            issues.append(Issue("agent_archived", "보관된 에이전트입니다", node.id))
        elif row.supports_function_calling is False:
            message = "이 에이전트의 모델은 도구 호출을 지원하지 않아 출력 형식을 맞출 수 없습니다"
            issues.append(Issue("model_no_tools", message, node.id))
    return issues


async def _can_run(context: OrgContext, row: Any) -> bool:
    """Legacy mode: the owner's own agents. Otherwise the OpenFGA ``can_run`` decision."""
    if current_authz().mode == "legacy":
        return bool(row.owned)
    try:
        await require_agent_permission(context, row.id, "can_run", resource_org_id=row.org_id)
    except AppError:
        return False
    return True


def definition_hash(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def publish(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID, *, revision: int
) -> PublishResult:
    """Freeze the draft at ``revision`` into the next version, pinning its agents."""
    workflow = await get_workflow(db, context, workflow_id, for_update=True)
    if workflow.archived_at is not None:
        raise _archived()
    if workflow.draft_revision != revision:
        raise _stale(workflow.draft_revision)
    definition = parse_definition(workflow.draft_definition)
    issues = graph_issues(definition) + await agent_issues(db, context, definition)
    if issues:
        raise AppError(
            "WORKFLOW_CHECK_FAILED",
            f"확인할 것 {len(issues)}개를 해결해야 게시할 수 있습니다",
            status=422,
            details={"issues": [issue.as_dict() for issue in issues]},
        )
    stored = definition.dump()
    agents = {
        node.id: uuid.UUID(node.agent_id)
        for node in definition.nodes
        if isinstance(node, AgentNode)
    }
    prints = await agent_fingerprints(db, agents.values())
    pins = {
        node_id: {"agent_id": str(agent_id), "fingerprint": prints[agent_id]}
        for node_id, agent_id in sorted(agents.items())
    }
    latest = await db.scalar(
        select(func.max(WorkflowVersion.version_no)).where(
            WorkflowVersion.workflow_id == workflow.id
        )
    )
    version = WorkflowVersion(
        workflow_id=workflow.id,
        org_id=workflow.org_id,
        tenant_id=workflow.tenant_id,
        version_no=(latest or 0) + 1,
        definition=stored,
        definition_hash=definition_hash(stored),
        agent_pins=pins,
        published_by=context.user_id,
    )
    db.add(version)
    workflow.published_version_no = version.version_no
    workflow.updated_at = _now()
    await db.flush()
    return PublishResult(workflow, version)


async def list_versions(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID
) -> list[WorkflowVersion]:
    workflow = await get_workflow(db, context, workflow_id)
    stmt = (
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow.id)
        .order_by(WorkflowVersion.version_no.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_version(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID, version_no: int
) -> WorkflowVersion:
    workflow = await get_workflow(db, context, workflow_id)
    version = (
        await db.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version_no == version_no,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise workflow_version_not_found()
    return version


@dataclass(frozen=True, slots=True)
class AgentUsage:
    workflow: Workflow
    in_draft: bool
    in_published: bool
    # The agent's settings no longer match the published pin: the next run is refused (Q1).
    drifted: bool


async def workflows_using_agent(
    db: AsyncSession, context: OrgContext, agent_id: uuid.UUID
) -> list[AgentUsage]:
    """The caller's workflows whose draft or published version uses the agent."""
    usages: list[AgentUsage] = []
    live: str | None = None
    for workflow in await list_workflows(db, context):
        in_draft = any(
            node.get("type") == "agent" and node.get("agent_id") == str(agent_id)
            for node in (workflow.draft_definition or {}).get("nodes", [])
        )
        pins: list[str] = []
        if workflow.published_version_no is not None:
            version = await db.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_id == workflow.id,
                    WorkflowVersion.version_no == workflow.published_version_no,
                )
            )
            pins = [
                pin["fingerprint"]
                for pin in (version.agent_pins if version else {}).values()
                if pin.get("agent_id") == str(agent_id)
            ]
        if not in_draft and not pins:
            continue
        if pins and live is None:
            live = (await agent_fingerprints(db, [agent_id])).get(agent_id, "")
        usages.append(AgentUsage(workflow, in_draft, bool(pins), any(pin != live for pin in pins)))
    return usages


async def archive(db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID) -> Workflow:
    """Idempotent: archiving an archived workflow keeps its first archive time."""
    workflow = await get_workflow(db, context, workflow_id, for_update=True)
    if workflow.archived_at is None:
        now = _now()
        workflow.archived_at = now
        workflow.updated_at = now
        await db.flush()
    return workflow


__all__ = [
    "AgentUsage",
    "PublishResult",
    "workflows_using_agent",
    "Workflow",
    "agent_issues",
    "archive",
    "check_workflow",
    "create_workflow",
    "definition_hash",
    "get_version",
    "get_workflow",
    "list_versions",
    "list_workflows",
    "parse_definition",
    "publish",
    "save_draft",
    "workflow_not_found",
]
