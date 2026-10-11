"""R10 F1 independent pre-effect witnesses; dedicated production imports only."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock

import pytest
from anyio.to_thread import run_sync
from fastapi import UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.agent_runtime.skill_builder import tools
from app.config import settings
from app.exceptions import AppError
from app.marketplace import mcp_server, publish_service
from app.marketplace.schemas import MarketplaceItemPatchIn, PublishMcpServerIn
from app.models.marketplace import MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.organization import OrganizationMember
from app.routers import uploads
from app.services import skill_builder_service, skill_draft_workspace
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_skill_builder_tools import DraftToolCase
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = [
    "tests.authz.test_private_skill_builder_tools",
    "tests.authz.test_private_skill_scope",
]
type Mutation = Literal["patch", "disable", "enable"]
type DraftTool = Literal["validate_skill", "generate_evals"]


def _files(root: Path) -> list[Path]:
    return list(root.rglob("*"))


async def suspend(db: AsyncSession, scopes: PrivateScopes) -> None:
    own = scopes.first
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="suspended")
    )
    await db.commit()


@pytest.mark.parametrize("mutation", ["patch", "disable", "enable"])
async def test_catalog_management_denies_revoked_authority(
    db: AsyncSession, private_scopes: PrivateScopes, org_fixture: OrgFixture, mutation: Mutation
) -> None:
    own = private_scopes.first
    item = MarketplaceItem(
        owner_user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="owned",
        slug="f1-owned",
        resource_type="mcp",
        visibility="private",
        status="disabled",
    )
    db.add(item)
    await db.commit()
    await publish_service.patch_item(
        db,
        item_id=item.id,
        user=org_fixture.user,
        context=own,
        body=MarketplaceItemPatchIn(description="positive-owned-control"),
    )
    await db.commit()
    await suspend(db, private_scopes)
    denied = False
    match mutation:
        case "patch":
            try:
                await publish_service.patch_item(
                    db,
                    item_id=item.id,
                    user=org_fixture.user,
                    context=own,
                    body=MarketplaceItemPatchIn(name="revoked-write"),
                )
            except AppError:
                denied = True
        case "disable":
            item.status = "published"
            await db.commit()
            try:
                await publish_service.disable_item(
                    db,
                    item_id=item.id,
                    user=org_fixture.user,
                    context=own,
                )
            except AppError:
                denied = True
        case "enable":
            try:
                await publish_service.enable_item(
                    db,
                    item_id=item.id,
                    user=org_fixture.user,
                    context=own,
                )
            except AppError:
                denied = True
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    stored = (
        await db.execute(
            select(MarketplaceItem.name, MarketplaceItem.status).where(
                MarketplaceItem.id == item.id
            )
        )
    ).one()
    assert denied and stored.name == "owned"
    assert stored.status == ("published" if mutation == "disable" else "disabled")


async def test_mcp_publish_denies_revoked_before_item_creation(
    db: AsyncSession, private_scopes: PrivateScopes, org_fixture: OrgFixture
) -> None:
    own = private_scopes.first
    server = McpServer(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="owned",
        transport="sse",
        url="https://example.invalid",
    )
    db.add(server)
    await db.commit()
    await mcp_server.publish_mcp_server(
        db,
        server_id=server.id,
        user=org_fixture.user,
        context=own,
        body=PublishMcpServerIn(name="owned-control", visibility="private"),
    )
    await db.commit()
    await suspend(db, private_scopes)
    denied = False
    try:
        await mcp_server.publish_mcp_server(
            db,
            server_id=server.id,
            user=org_fixture.user,
            context=own,
            body=PublishMcpServerIn(name="revoked-publish", visibility="private"),
        )
    except AppError:
        denied = True
    await db.commit()
    created = await db.scalar(
        select(MarketplaceItem.id).where(MarketplaceItem.name == "revoked-publish")
    )
    assert denied and created is None


async def test_upload_creator_denies_revoked_before_file_and_row(
    db: AsyncSession, private_scopes: PrivateScopes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    own = private_scopes.first
    await suspend(db, private_scopes)
    denied = False
    upload = UploadFile(
        BytesIO(b"dummy"), filename="f1.txt", headers=Headers({"content-type": "text/plain"})
    )
    created = None
    try:
        created = await uploads.create_upload(file=upload, db=db, context=own, _csrf=None)
    except AppError:
        denied = True
    files = await run_sync(_files, tmp_path / "uploads")
    assert denied and created is None and files == []


@pytest.mark.parametrize("tool_name", ["validate_skill", "generate_evals"])
async def test_bound_tool_denies_foreign_source_before_effects(
    db: AsyncSession,
    draft_tool_case: DraftToolCase,
    skill_case: SkillCase,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: DraftTool,
) -> None:
    case = draft_tool_case
    case.session.source_skill_id = skill_case.second.id
    await db.commit()
    read_files = Mock(return_value=[])
    monkeypatch.setattr(skill_draft_workspace, "load_draft_files", read_files)
    monkeypatch.setattr(skill_draft_workspace, "resolve_workspace_dir", lambda _: case.workspace)
    bound = tools.build_skill_builder_tools(
        session_id=str(case.session.id),
        workspace_path=case.bound_path,
        session_factory=TestSession,
        context=case.context,
    )
    tool = next(item for item in bound if item.name == tool_name)
    denied = False
    try:
        await tool.ainvoke({})
    except AppError:
        denied = True
    await db.refresh(case.session)
    wrote_eval = await run_sync((case.workspace / "evals/evals.json").exists)
    assert denied and read_files.call_count == 0
    assert case.session.validation_result is None and not wrote_eval


async def test_session_list_excludes_persisted_foreign_source(
    db: AsyncSession, draft_tool_case: DraftToolCase, skill_case: SkillCase
) -> None:
    case = draft_tool_case
    case.session.source_skill_id = skill_case.second.id
    await db.commit()
    assert await skill_builder_service.get_session(db, case.session.id, case.context) is None
    rows = await skill_builder_service.list_sessions(db, context=case.context)
    assert case.session.id not in {row.id for row in rows}


@pytest.mark.parametrize("tool_name", ["validate_skill", "generate_evals"])
async def test_bound_tool_owned_source_control(
    db: AsyncSession,
    draft_tool_case: DraftToolCase,
    skill_case: SkillCase,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: DraftTool,
) -> None:
    case = draft_tool_case
    case.session.source_skill_id = skill_case.first.id
    await db.commit()
    read_files = Mock(return_value=[])
    monkeypatch.setattr(skill_draft_workspace, "load_draft_files", read_files)
    monkeypatch.setattr(skill_draft_workspace, "resolve_workspace_dir", lambda _: case.workspace)
    bound = tools.build_skill_builder_tools(
        session_id=str(case.session.id),
        workspace_path=case.bound_path,
        session_factory=TestSession,
        context=case.context,
    )
    tool = next(item for item in bound if item.name == tool_name)
    await tool.ainvoke({})
    await db.refresh(case.session)
    assert read_files.call_count == 1
    match tool_name:
        case "validate_skill":
            assert case.session.validation_result is not None
        case "generate_evals":
            assert await run_sync((case.workspace / "evals/evals.json").is_file)
        case unreachable:
            assert_never(unreachable)
