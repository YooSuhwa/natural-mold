"""MCP Apps public authorization must precede SQL writes; no proxy calls."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp import apps_binding_store
from app.models.organization import OrganizationMember
from app.models.skill import Skill

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_mcp_apps_scope import McpAppCase

pytest_plugins = ["tests.authz.test_private_mcp_apps_scope"]
SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("change", ["foreign_context", "membership", "missing_binding"])
async def test_mcp_app_authorization_denies_before_pending_private_sql(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    mcp_app_case: McpAppCase,
    change: Literal["foreign_context", "membership", "missing_binding"],
) -> None:
    assert Path(apps_binding_store.__file__).is_relative_to(SOURCE_ROOT)
    own, foreign = private_scopes.first, private_scopes.second
    row = Skill(
        user_id=foreign.user_id,
        org_id=foreign.org_id,
        tenant_id=foreign.tenant_id,
        name="Foreign original",
        slug="f4-mcp-foreign",
        kind="text",
    )
    db.add(row)
    await db.commit()
    if change == "membership":
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id,
                OrganizationMember.org_id == own.org_id,
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    target_id = row.id
    row.name = "MCP UNAUTHORIZED PRE-DENIAL WRITE"
    context = foreign if change == "foreign_context" else own
    tool_call_id = "missing" if change == "missing_binding" else "fixture-call"
    flush = Mock()
    event.listen(db.sync_session, "before_flush", flush)
    try:
        with pytest.raises(apps_binding_store.McpAppsError) as denied:
            await apps_binding_store.authorize_binding(
                db,
                conversation_id=mcp_app_case.conversation_id,
                run_id=mcp_app_case.run_id,
                tool_call_id=tool_call_id,
                context=context,
            )
        assert denied.value.status_code == 404
    finally:
        event.remove(db.sync_session, "before_flush", flush)
    with db.no_autoflush:
        stored = await db.scalar(select(Skill.name).where(Skill.id == target_id))
    remains_staged = row in db.dirty
    await db.rollback()
    print(
        f"F4 mcp change={change} denied=404 flushes={flush.call_count} "
        f"stored={stored!r} remains_staged={remains_staged}"
    )
    assert flush.call_count == 0
    assert stored == "Foreign original"
    assert remains_staged
