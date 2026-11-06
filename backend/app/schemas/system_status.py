"""The operator's system status view (app/install)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.install.profiles import ComponentKey, ProfileKey, Requirement

Found = Literal["ok", "off", "error"]
Verdict = Literal["ok", "warning", "error"]


class ComponentStatusOut(BaseModel):
    key: ComponentKey
    requirement: Requirement
    found: Found
    verdict: Verdict
    detail: str


class WorkerOut(BaseModel):
    worker_id: str
    hostname: str
    job_types: list[str]
    started_at: datetime
    heartbeat_at: datetime
    live: bool


class QueueOut(BaseModel):
    queued: int
    running: int
    overdue: int
    failed_last_day: int


class SystemStatusOut(BaseModel):
    profile: ProfileKey
    verdict: Verdict
    components: list[ComponentStatusOut]
    workers: list[WorkerOut]
    queue: QueueOut
    checked_at: datetime
