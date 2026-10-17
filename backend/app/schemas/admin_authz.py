"""Operator authorization-sync screen bodies (plan 14.3, S27; H-04).

Counts, ids and timestamps only: failure rows name the company and the resource id,
never a resource name or tuple content (S27 "상태").
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

FgaStatus = Literal["ok", "degraded", "down", "disabled"]
RunStatus = Literal["queued", "running", "succeeded", "failed", "skipped"]


class _Body(BaseModel):
    model_config = ConfigDict(frozen=True)


class FgaHealthView(_Body):
    status: FgaStatus
    latency_ms: float | None = None
    checked_at: datetime | None = None
    last_success_at: datetime | None = None


class OutboxStats(_Body):
    pending: int
    retrying: int  # pending rows with at least one failed attempt
    failed: int  # pending rows at or past the alert attempt count
    lag_seconds: float


class RevocationStats(_Body):
    pending: int
    stuck: int  # pending past the 10-minute alert threshold
    oldest_seconds: float


class ReconcileCounts(_Body):
    expected: int = 0
    stored: int = 0
    missing: int = 0
    extra: int = 0
    changed: int = 0
    held: int = 0
    queued: int = 0
    failed: int = 0
    diff: int = 0
    repaired: int = 0  # queued rows the run itself applied (operator runs drain)
    deferred: int = 0  # queued rows still pending (left to the outbox drain or failed)


class ReconcileRunView(_Body):
    id: UUID
    status: RunStatus
    trigger: Literal["manual", "scheduled"]
    mode: str
    partitions_done: int
    partitions_total: int
    report: ReconcileCounts | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ReconcileStatus(_Body):
    active: ReconcileRunView | None = None
    last: ReconcileRunView | None = None


class ShadowTypeCount(_Body):
    type: str
    legacy_only: int  # legacy allowed, OpenFGA would deny
    fga_only: int  # OpenFGA would allow, legacy denied


class DelegationStats(_Body):
    broken_links: int
    broken_last_24h: int


class RetentionStats(_Body):
    enabled: bool
    schedule: str
    last_run_at: datetime | None = None
    purges_last_24h: int


class JobStatus(_Body):
    id: str
    scheduled: bool
    schedule: str | None = None


class AuthzOpsStatus(_Body):
    mode: str
    sync_enabled: bool  # outside legacy with a pinned store and model (C-05 jobs)
    openfga: FgaHealthView
    outbox: OutboxStats
    revocations: RevocationStats
    reconcile: ReconcileStatus
    shadow: list[ShadowTypeCount]
    delegations: DelegationStats
    retention: RetentionStats
    jobs: list[JobStatus]


class OutboxFailure(_Body):
    id: UUID
    tenant_id: UUID | None = None
    resource_type: str
    resource_id: str
    op: str
    attempts: int
    error_type: str | None = None
    created_at: datetime
    next_attempt_at: datetime


class OutboxFailurePage(_Body):
    items: list[OutboxFailure]


class ReconcileStarted(_Body):
    job_id: UUID
