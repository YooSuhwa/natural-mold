"""Model override resolution guards for Agent Blueprint materialization.

Adversarial security review found two issues in
``agent_blueprint_service._resolve_model_id``:

1. A caller-supplied ``model_id`` override resolved *any* model row,
   including operator-hidden (``is_visible=False``) models — a hidden
   model could be forced into a materialized agent.
2. A missing override returned ``model_not_found`` (404) while other
   validation failures return 4xx with different shapes, giving an
   enumeration oracle (existence vs other failure leaked via status).

The fix rejects a missing or hidden override uniformly with
``marketplace_invalid_package`` (422) and never resolves a hidden model.
This file pins:

* (a) hidden model override → 422 ``MARKETPLACE_INVALID_PACKAGE``
* (b) missing model override → 422 ``MARKETPLACE_INVALID_PACKAGE``
* (c) visible model override → materializes the agent
* spec ``preferred_model_id`` pointing at a hidden model is ignored
  (falls back to provider/model_name matching).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.marketplace.schemas import CreateAgentFromBlueprintIn
from app.models.agent_blueprint import AgentBlueprint
from app.models.model import Model
from app.models.user import User
from app.services import agent_blueprint_service
from tests.conftest import TEST_USER_ID


async def _ensure_test_user(db: AsyncSession) -> None:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        db.add(
            User(
                id=TEST_USER_ID,
                email="test@test.com",
                name="Test User",
                hashed_password="h",
                is_active=True,
                is_super_user=True,
            )
        )
        await db.flush()


async def _make_blueprint(
    db: AsyncSession,
    *,
    model_spec: dict | None = None,
) -> AgentBlueprint:
    spec = {
        "schema_version": 1,
        "resource": "agent_blueprint",
        "agent": {
            "name": "Resolve Agent",
            "description": "Resolves a model",
            "system_prompt": "You help.",
            "model": model_spec
            if model_spec is not None
            else {"provider": "openai", "model_name": "gpt-5-mini"},
        },
        "capabilities": {"tools": [], "skills": [], "mcp_tools": [], "subagents": []},
        "setup": {"required_credentials": [], "warnings": [], "blocked_dependencies": []},
    }
    blueprint = AgentBlueprint(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="Resolve Agent",
        description="Resolves a model",
        spec=spec,
        spec_hash="d" * 64,
        origin_kind="imported_by_me",
        install_status="active",
    )
    db.add(blueprint)
    await db.flush()
    return blueprint


def _model(*, is_visible: bool, model_name: str) -> Model:
    return Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name=model_name,
        display_name=model_name,
        is_default=False,
        is_visible=is_visible,
    )


@pytest.mark.asyncio
async def test_hidden_model_override_rejected_422(db: AsyncSession) -> None:
    await _ensure_test_user(db)
    visible = _model(is_visible=True, model_name="gpt-5-mini")
    hidden = _model(is_visible=False, model_name="gpt-5-internal")
    db.add_all([visible, hidden])
    blueprint = await _make_blueprint(db)
    await db.flush()

    with pytest.raises(AppError) as exc:
        await agent_blueprint_service.create_agent_from_blueprint(
            db,
            blueprint_id=blueprint.id,
            user_id=TEST_USER_ID,
            body=CreateAgentFromBlueprintIn(model_id=hidden.id),
        )

    assert exc.value.code == "MARKETPLACE_INVALID_PACKAGE"
    # No model-existence leak — message must not confirm the row exists.
    assert "not available" in exc.value.message


@pytest.mark.asyncio
async def test_missing_model_override_rejected_422(db: AsyncSession) -> None:
    await _ensure_test_user(db)
    visible = _model(is_visible=True, model_name="gpt-5-mini")
    db.add(visible)
    blueprint = await _make_blueprint(db)
    await db.flush()

    with pytest.raises(AppError) as exc:
        await agent_blueprint_service.create_agent_from_blueprint(
            db,
            blueprint_id=blueprint.id,
            user_id=TEST_USER_ID,
            body=CreateAgentFromBlueprintIn(model_id=uuid.uuid4()),
        )

    # Same status/code as the hidden case — existence vs visibility must
    # not be distinguishable from the response (enumeration oracle guard).
    assert exc.value.code == "MARKETPLACE_INVALID_PACKAGE"
    assert "not available" in exc.value.message


@pytest.mark.asyncio
async def test_visible_model_override_materializes_agent(db: AsyncSession) -> None:
    await _ensure_test_user(db)
    visible = _model(is_visible=True, model_name="gpt-5-mini")
    other_visible = _model(is_visible=True, model_name="gpt-5-pro")
    db.add_all([visible, other_visible])
    blueprint = await _make_blueprint(db)
    await db.flush()

    agent = await agent_blueprint_service.create_agent_from_blueprint(
        db,
        blueprint_id=blueprint.id,
        user_id=TEST_USER_ID,
        body=CreateAgentFromBlueprintIn(model_id=other_visible.id),
    )

    assert agent.model_id == other_visible.id


@pytest.mark.asyncio
async def test_spec_preferred_model_id_ignores_hidden_model(db: AsyncSession) -> None:
    """A spec ``preferred_model_id`` aimed at a hidden model must not pull
    it in — resolution falls back to provider/model_name matching against
    a visible row instead."""

    await _ensure_test_user(db)
    visible = _model(is_visible=True, model_name="gpt-5-mini")
    hidden = _model(is_visible=False, model_name="gpt-5-mini")
    db.add_all([visible, hidden])
    blueprint = await _make_blueprint(
        db,
        model_spec={
            "preferred_model_id": str(hidden.id),
            "provider": "openai",
            "model_name": "gpt-5-mini",
        },
    )
    await db.flush()

    agent = await agent_blueprint_service.create_agent_from_blueprint(
        db,
        blueprint_id=blueprint.id,
        user_id=TEST_USER_ID,
        body=CreateAgentFromBlueprintIn(),
    )

    # Fell back to the visible row, not the hidden ``preferred_model_id``.
    assert agent.model_id == visible.id
