"""Detached Builder node writes retain the checkpoint's private actor tuple."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.builder_v3.nodes import phase7_save, phase8_build
from app.agent_runtime.builder_v3.state import BuilderState
from app.exceptions import AppError
from app.models.builder_session import BuilderSession
from app.models.organization import OrganizationMember
from app.schemas.builder import BuilderStatus
from app.services import builder_service
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]
Operation = Literal["save", "error"]


@dataclass(frozen=True, slots=True)
class NodeScenario:
    session: BuilderSession
    state: BuilderState
    denied: bool


@pytest.fixture(params=["own", "revoked", "moved", "wrong_user", "missing_scope"])
async def node_scenario(
    db: AsyncSession, private_scopes: PrivateScopes, request: pytest.FixtureRequest
) -> NodeScenario:
    context = private_scopes.first
    row = await builder_service.create_session(db, context, "Detached Builder fixture")
    state: BuilderState = {
        "session_id": str(row.id),
        "user_id": str(context.user_id),
        "org_id": str(context.org_id),
        "tenant_id": str(context.tenant_id),
        "intent": {
            "agent_name": "Scope fixture",
            "agent_name_ko": "범위 검증",
            "agent_description": "Fixture",
            "primary_task_type": "Fixture",
            "use_cases": ["Fixture"],
        },
        "system_prompt": "Fixture",
    }
    match request.param:
        case "own":
            pass
        case "revoked":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == context.org_id,
                    OrganizationMember.user_id == context.user_id,
                )
            )
        case "moved":
            row.org_id = private_scopes.second.org_id
            row.tenant_id = private_scopes.second.tenant_id
        case "wrong_user":
            state["user_id"] = str(private_scopes.second.org_id)
        case "missing_scope":
            del state["org_id"]
        case _:
            pytest.fail("Unknown node fixture")
    await db.commit()
    return NodeScenario(row, state, request.param != "own")


async def _execute(state: BuilderState, operation: Operation) -> None:
    match operation:
        case "save":
            await phase7_save.phase7_save(state)
        case "error":
            await phase8_build._persist_error(state, "Fixture failure")


@pytest.mark.parametrize("operation", ["save", "error"])
async def test_builder_node_authorizes_before_write(
    db: AsyncSession, node_scenario: NodeScenario, operation: Operation
) -> None:
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(phase7_save, "async_session_factory", TestSession)
        patches.setattr(phase8_build, "async_session_factory", TestSession)
        if node_scenario.denied:
            with pytest.raises(AppError):
                await _execute(node_scenario.state, operation)
        else:
            await _execute(node_scenario.state, operation)
    await db.refresh(node_scenario.session)
    expected = BuilderStatus.BUILDING
    if not node_scenario.denied:
        expected = BuilderStatus.PREVIEW if operation == "save" else BuilderStatus.FAILED
    assert node_scenario.session.status == expected
    if node_scenario.denied:
        assert node_scenario.session.draft_config is None
        assert node_scenario.session.error_message is None
