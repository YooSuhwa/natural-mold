"""Workflow management API (workflow-plan W1). Owner only in 1차 (Q5).

Runs arrive with the runner (W2). The Dify-compatible ``POST /workflows/run`` (one agent)
is a different, older path (``agent_runtime_api``); this API lives under ``/api/workflows``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.schemas.workflow import (
    WorkflowAgentUsage,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowDraftUpdate,
    WorkflowIssue,
    WorkflowIssues,
    WorkflowPublish,
    WorkflowRunDetail,
    WorkflowRunStart,
    WorkflowRunSummary,
    WorkflowStepRunOut,
    WorkflowSummary,
    WorkflowVersionDetail,
    WorkflowVersionSummary,
)
from app.services import audit_service
from app.workflows import runner, service

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
runs_router = APIRouter(prefix="/api/workflow-runs", tags=["workflows"])

WORKFLOWS = PRIVATE("workflows")
WORKFLOW = PRIVATE("workflows", param="workflow_id")
RUN = PRIVATE("workflow_runs", param="run_id")


async def _audit(
    db: AsyncSession,
    user: CurrentUser,
    request: Request,
    action: str,
    workflow: service.Workflow,
    metadata: dict[str, object] | None = None,
) -> None:
    await audit_service.record_self_event(
        db,
        user,
        action=action,
        target_type="workflow",
        target_id=workflow.id,
        target_name=workflow.name,
        request=request,
        metadata=metadata,
    )


@router.get("", response_model=list[WorkflowSummary])
@authz_policy(WORKFLOWS)
async def list_workflows(
    include_archived: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    return await service.list_workflows(db, context, include_archived=include_archived)


@router.get("/using-agent/{agent_id}", response_model=list[WorkflowAgentUsage])
@authz_policy(WORKFLOWS)
async def workflows_using_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    """Shown on the agent's settings: changing it means republishing these (Q1)."""
    return [
        WorkflowAgentUsage(
            id=usage.workflow.id,
            name=usage.workflow.name,
            published_version_no=usage.workflow.published_version_no,
            in_draft=usage.in_draft,
            in_published=usage.in_published,
            drifted=usage.drifted,
        )
        for usage in await service.workflows_using_agent(db, context, agent_id)
    ]


@router.post("", response_model=WorkflowDetail, status_code=201)
@authz_policy(WORKFLOWS)
async def create_workflow(
    body: WorkflowCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    workflow = await service.create_workflow(
        db, context, name=body.name, description=body.description
    )
    await _audit(db, user, request, audit_actions.WORKFLOW_CREATE, workflow)
    await db.commit()
    return workflow


@router.get("/{workflow_id}", response_model=WorkflowDetail)
@authz_policy(WORKFLOW)
async def get_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    return await service.get_workflow(db, context, workflow_id)


@router.put("/{workflow_id}/draft", response_model=WorkflowDetail)
@authz_policy(WORKFLOW)
async def save_draft(
    workflow_id: uuid.UUID,
    body: WorkflowDraftUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    workflow = await service.save_draft(
        db,
        context,
        workflow_id,
        revision=body.revision,
        definition=body.definition,
        name=body.name,
        description=body.description,
    )
    await db.commit()
    return workflow


@router.get("/{workflow_id}/issues", response_model=WorkflowIssues)
@authz_policy(WORKFLOW)
async def check_workflow(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    workflow = await service.get_workflow(db, context, workflow_id)
    issues = await service.check_workflow(db, context, workflow)
    return WorkflowIssues(
        draft_revision=workflow.draft_revision,
        issues=[WorkflowIssue.model_validate(issue.as_dict()) for issue in issues],
    )


@router.post("/{workflow_id}/publish", response_model=WorkflowVersionDetail, status_code=201)
@authz_policy(WORKFLOW)
async def publish_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowPublish,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    result = await service.publish(db, context, workflow_id, revision=body.revision)
    await _audit(
        db,
        user,
        request,
        audit_actions.WORKFLOW_PUBLISH,
        result.workflow,
        metadata={
            "version_no": result.version.version_no,
            "definition_hash": result.version.definition_hash,
            "agent_ids": sorted({pin["agent_id"] for pin in result.version.agent_pins.values()}),
        },
    )
    await db.commit()
    return result.version


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionSummary])
@authz_policy(WORKFLOW)
async def list_versions(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    return await service.list_versions(db, context, workflow_id)


@router.get("/{workflow_id}/versions/{version_no}", response_model=WorkflowVersionDetail)
@authz_policy(WORKFLOW)
async def get_version(
    workflow_id: uuid.UUID,
    version_no: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    return await service.get_version(db, context, workflow_id, version_no)


@router.post("/{workflow_id}/archive", response_model=WorkflowDetail)
@authz_policy(WORKFLOW)
async def archive_workflow(
    workflow_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    already = (await service.get_workflow(db, context, workflow_id)).archived_at is not None
    workflow = await service.archive(db, context, workflow_id)
    if not already:
        await _audit(db, user, request, audit_actions.WORKFLOW_ARCHIVE, workflow)
    await db.commit()
    return workflow


@router.post("/{workflow_id}/runs", response_model=WorkflowRunSummary, status_code=201)
@authz_policy(WORKFLOW)
async def start_run(
    workflow_id: uuid.UUID,
    body: WorkflowRunStart,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    run = await runner.start_run(db, context, workflow_id, body.inputs)
    await audit_service.record_self_event(
        db,
        user,
        action=runner.RUN_STARTED,
        target_type="workflow_run",
        target_id=run.id,
        request=request,
        metadata={"workflow_id": str(workflow_id), "version_id": str(run.version_id)},
    )
    await db.commit()
    return _summary(run, await runner.version_numbers(db, [run]))


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunSummary])
@authz_policy(WORKFLOW)
async def list_runs(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    runs = await runner.list_runs(db, context, workflow_id)
    numbers = await runner.version_numbers(db, runs)
    return [_summary(run, numbers) for run in runs]


def _summary(run: runner.WorkflowRun, numbers: dict[uuid.UUID, int]) -> WorkflowRunSummary:
    summary = WorkflowRunSummary.model_validate(run)
    return summary.model_copy(update={"version_no": numbers.get(run.version_id)})


async def _detail(db: AsyncSession, run: runner.WorkflowRun) -> WorkflowRunDetail:
    steps = await runner.list_steps(db, run)
    return WorkflowRunDetail.model_validate(
        {
            **_summary(run, await runner.version_numbers(db, [run])).model_dump(),
            "input": run.input,
            "output": run.output,
            "steps": [WorkflowStepRunOut.model_validate(step) for step in steps],
        }
    )


@runs_router.get("/{run_id}", response_model=WorkflowRunDetail)
@authz_policy(RUN)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
):
    return await _detail(db, await runner.get_run(db, context, run_id))


@runs_router.post("/{run_id}/cancel", response_model=WorkflowRunDetail)
@authz_policy(RUN)
async def cancel_run(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
):
    before = (await runner.get_run(db, context, run_id)).status
    run = await runner.cancel_run(db, context, run_id)
    if before != run.status:
        await audit_service.record_self_event(
            db,
            user,
            action=runner.RUN_CANCELED,
            target_type="workflow_run",
            target_id=run.id,
            request=request,
            metadata={"workflow_id": str(run.workflow_id), "status_before": before},
        )
    await db.commit()
    return await _detail(db, run)
