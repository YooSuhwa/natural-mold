"""Operational activation rejects inconsistent real-observation chronology."""

import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from schema_drafts.org_authz.rollout_gate import require_rollout_receipt


@pytest.mark.parametrize("stabilization", [False, True])
def test_activation_rejects_enforcement_end_before_start(
    tmp_path: Path, stabilization: bool
) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "version": 1,
                "backup_restored": True,
                "backup_sha256": "a" * 64,
                "shadow_started_at": "2026-01-01T00:00:00Z",
                "shadow_ended_at": "2026-01-15T00:00:00Z",
                "shadow_requests": 500,
                "shadow_mismatches": 0,
                "shadow_errors": 0,
                "all_types_enforced": True,
                "enforcement_started_at": "2026-01-16T00:00:00Z",
                "enforcement_ended_at": "2026-01-15T00:00:00Z",
                "enforcement_errors_within_budget": True,
            }
        )
    )
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "org_authz_rollout_receipt": str(receipt),
                },
            )
            with (
                Operations.context(context),
                pytest.raises(ValueError, match="cannot end before"),
            ):
                require_rollout_receipt(stabilization=stabilization)
    finally:
        engine.dispose()
