"""Frozen lifecycle boundary DTOs expose only approved governance metadata."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.authz.error_codes import (
    agent_archived,
    agent_history_snapshot_incomplete,
    agent_lifecycle_execution_pending,
    agent_lifecycle_permission_required,
    agent_not_archived,
    agent_purge_name_mismatch,
)
from app.exception_handlers import register_exception_handlers
from app.schemas.agent_lifecycle import (
    AgentLifecycleMetadataResponse,
    AgentPurgeReceiptResponse,
    AgentPurgeRequest,
)

METADATA_KEYS = {
    "agent_id",
    "name",
    "owner_user_id",
    "owner_label",
    "archived_by_user_id",
    "archived_at",
    "lifecycle_state",
    "policy_delete_at",
    "can_archive",
    "can_restore",
    "can_purge",
}


def test_governance_metadata_has_exact_allowlist_and_is_frozen() -> None:
    now = datetime.now(UTC)
    payload = AgentLifecycleMetadataResponse(
        agent_id=uuid4(),
        name="Governed agent",
        owner_user_id=uuid4(),
        owner_label="Owner label",
        archived_by_user_id=uuid4(),
        archived_at=now,
        lifecycle_state="archived_stopped",
        policy_delete_at=None,
        can_archive=False,
        can_restore=True,
        can_purge=True,
    )

    assert set(payload.model_dump(mode="json")) == METADATA_KEYS
    assert not METADATA_KEYS.intersection(
        {"system_prompt", "model_params", "tools", "conversations", "runtime_policy"}
    )
    with pytest.raises(ValidationError):
        AgentLifecycleMetadataResponse.model_validate(
            {**payload.model_dump(), "system_prompt": "must never cross this boundary"}
        )
    with pytest.raises(ValidationError):
        payload.__setattr__("name", "mutated")


def test_purge_request_preserves_exact_unicode_and_forbids_extra_fields() -> None:
    confirmation = "Ａgent e\u0301  "
    request = AgentPurgeRequest(confirmation_name=confirmation)

    assert request.confirmation_name == confirmation
    with pytest.raises(ValidationError):
        AgentPurgeRequest.model_validate({"confirmation_name": confirmation, "force": True})


def test_purge_receipt_has_stable_frozen_contract() -> None:
    now = datetime.now(UTC)
    receipt = AgentPurgeReceiptResponse(
        receipt_id=uuid4(),
        agent_id=uuid4(),
        state="cleanup_pending",
        created_at=now,
        updated_at=now,
    )

    assert set(receipt.model_dump(mode="json")) == {
        "receipt_id",
        "agent_id",
        "state",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize(
    ("factory", "status", "code"),
    [
        (agent_archived, 409, "AGENT_ARCHIVED"),
        (agent_not_archived, 409, "AGENT_NOT_ARCHIVED"),
        (
            agent_lifecycle_execution_pending,
            409,
            "AGENT_LIFECYCLE_EXECUTION_PENDING",
        ),
        (agent_purge_name_mismatch, 409, "AGENT_PURGE_NAME_MISMATCH"),
        (
            agent_history_snapshot_incomplete,
            409,
            "AGENT_HISTORY_SNAPSHOT_INCOMPLETE",
        ),
        (
            agent_lifecycle_permission_required,
            403,
            "AGENT_LIFECYCLE_PERMISSION_REQUIRED",
        ),
    ],
)
async def test_lifecycle_errors_keep_exact_asgi_envelopes(factory, status: int, code: str) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe")
    async def probe() -> None:
        raise factory()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/probe")

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_purge_request_validation_remains_422_at_asgi_boundary() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/purge")
    async def purge(_: AgentPurgeRequest) -> bool:
        return True

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/purge", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
