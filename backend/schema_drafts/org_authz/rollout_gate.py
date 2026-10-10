"""Deferred destructive drafts require real observation and backup/restore receipts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from alembic import op


class RolloutReceipt(BaseModel):
    version: Literal[1]
    backup_restored: Literal[True]
    backup_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shadow_started_at: AwareDatetime
    shadow_ended_at: AwareDatetime
    shadow_requests: int = Field(gt=0)
    shadow_mismatches: Literal[0]
    shadow_errors: Literal[0]
    all_types_enforced: Literal[True]
    enforcement_started_at: AwareDatetime
    enforcement_ended_at: AwareDatetime
    enforcement_errors_within_budget: Literal[True]


def require_rollout_receipt(*, stabilization: bool) -> None:
    context = op.get_context()
    if context.opts.get("org_authz_disposable_validation") is True:
        # Explicit prototype bypass accepts only memory SQLite or owned lane/lab
        # DB prefixes. This is never evidence of production observation.
        connection = op.get_bind()
        if connection.dialect.name == "sqlite" and connection.engine.url.database in (
            None,
            "",
            ":memory:",
        ):
            return
        if connection.dialect.name == "postgresql":
            database = connection.engine.url.database or ""
            if database.startswith(("moldy_pg_lane_", "moldy_orgauthz_checkpoint_")):
                return
        raise ValueError("Disposable migration validation requires an owned test database")
    path = context.opts.get("org_authz_rollout_receipt")
    if not isinstance(path, str):
        raise ValueError("Deferred migration requires an actual org-authz rollout receipt")
    receipt = RolloutReceipt.model_validate_json(Path(path).read_text())
    if receipt.shadow_ended_at - receipt.shadow_started_at < timedelta(days=14):
        raise ValueError("Fourteen days of actual shadow observation are required")
    if receipt.enforcement_started_at < receipt.shadow_ended_at:
        raise ValueError("Enforcement must follow shadow observation")
    if receipt.enforcement_ended_at > datetime.now(UTC):
        raise ValueError("Future-dated observation is not evidence")
    if stabilization and receipt.enforcement_ended_at - receipt.enforcement_started_at < timedelta(
        days=14
    ):
        raise ValueError("Fourteen days of actual enforcement stabilization are required")
