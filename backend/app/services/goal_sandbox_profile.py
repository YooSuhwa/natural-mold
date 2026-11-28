"""Identify an immutable OpenSandbox deployment and match its measured workload."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.config import settings
from app.skills.package_hash import digest_package_files

_Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_Cost = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
_PositiveTime = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class _Workload(BaseModel):
    skill_tree_sha256: _Hash
    skill_tree_sha256s: tuple[_Hash, _Hash] | None = None
    input_sha256: _Hash
    input_bytes: Annotated[int, Field(gt=0)]


class _Arm(BaseModel):
    sandbox_ids: list[str]
    total_seconds: _PositiveTime
    intermediate_sha256: _Hash
    result_sha256: _Hash


class _Comparison(BaseModel):
    per_call: _Arm
    retained: _Arm


class _Totals(BaseModel):
    per_call: _Cost
    retained: _Cost


class _Billing(BaseModel):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    evidence_ref: Annotated[str, Field(min_length=1)]
    totals: _Totals


class _Report(BaseModel):
    schema_version: Literal[1]
    provider: Literal["opensandbox"]
    run_id: UUID
    profile: dict[str, object]
    workload: _Workload
    arms: _Comparison
    cost: _Billing
    passes_time_and_cost: Literal[True]


def skill_tree_digest(files: Sequence[tuple[str, bytes]]) -> str:
    """Cover every byte sent to the provider, including unexpected extra files."""
    return digest_package_files(sorted(files, key=lambda item: item[0]))


def _profile() -> dict[str, object]:
    return {
        "domain": settings.skill_sandbox_domain,
        "image": settings.skill_sandbox_image,
        "cpu": settings.skill_sandbox_cpu,
        "memory": settings.skill_sandbox_memory,
        "network_domains": [],
    }


def goal_sandbox_provider_fingerprint() -> str:
    configuration = {**_profile(), "use_server_proxy": settings.skill_sandbox_use_server_proxy}
    data = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def verify_package_pair(report: dict[str, Any]) -> None:
    """Validate both packages, their per-stage resources, and private file retention."""
    workload = report.get("workload", {})
    if "skill_tree_sha256s" not in workload:
        return  # Existing single-package measurements precede per-stage records.
    pair = workload["skill_tree_sha256s"]
    if (
        not isinstance(pair, list)
        or len(pair) != 2
        or any(
            not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest)
            for digest in pair
        )
        or workload.get("skill_tree_sha256") != pair[0]
    ):
        raise ValueError("Require the two ordered package digests for the measured workflow.")
    if pair[0] != pair[1] and workload.get("private_intermediate") is not True:
        raise ValueError("Different packages require private-intermediate measurements.")
    for name, arm in report["arms"].items():
        stages = arm.get("stages", [])
        if [stage.get("skill_tree_sha256") for stage in stages] != pair:
            raise ValueError(
                "The two measured stages must use the same ordered packages in both arms."
            )
        if [stage.get("label") for stage in stages] != ["edit", "verify"]:
            raise ValueError("The measured stage names must match the paired workflow.")
        ids = arm["sandbox_ids"] if name == "per_call" else arm["sandbox_ids"] * 2
        if [stage.get("sandbox_id") for stage in stages] != ids:
            raise ValueError("Each stage needs its corresponding measured resource ID.")
        if any(
            not math.isfinite(stage["total_seconds"]) or stage["total_seconds"] <= 0
            for stage in stages
        ):
            raise ValueError("Each measured stage must have positive duration.")
        if (
            name == "retained"
            and workload.get("private_intermediate")
            and stages[0]["downloaded_bytes"] != 0
        ):
            raise ValueError("Private intermediate bytes appeared in the retained public outputs.")


def measured_skill_hashes(skill_hash: str) -> tuple[str, ...] | None:
    """Only a configured operator artifact can authorize its exact measured profile."""
    path = settings.goal_sandbox_evidence_path
    if (
        path is None
        or not path.is_absolute()
        or not path.is_file()
        or path.resolve().is_relative_to(Path(settings.data_root).resolve())
    ):
        return None
    if not re.fullmatch(r"(?:[^@\s]+@)?sha256:[0-9a-f]{64}", settings.skill_sandbox_image):
        return None
    try:
        # Reports contain aggregates and three resource IDs; read a bounded file.
        with path.open("rb") as stream:
            raw = stream.read(65_537)
        if len(raw) > 65_536:
            return None
        payload = json.loads(raw)
        report = _Report.model_validate(payload)
        verify_package_pair(payload)
    except (AttributeError, OSError, KeyError, TypeError, ValueError):
        return None
    base, retained = report.arms.per_call, report.arms.retained
    costs = report.cost.totals
    ids = [*base.sandbox_ids, *retained.sandbox_ids]
    hashes = report.workload.skill_tree_sha256s or (report.workload.skill_tree_sha256,)
    valid = (
        report.profile == _profile()
        and hashes[0] == report.workload.skill_tree_sha256
        and skill_hash in hashes
        and bool(report.cost.evidence_ref.strip())
        and len(base.sandbox_ids) == 2
        and len(retained.sandbox_ids) == 1
        and all(ids)
        and len(set(ids)) == 3
        and base.intermediate_sha256
        == base.result_sha256
        == retained.intermediate_sha256
        == retained.result_sha256
        and retained.total_seconds <= base.total_seconds
        and costs.retained <= costs.per_call
        and (retained.total_seconds < base.total_seconds or costs.retained < costs.per_call)
    )
    return tuple(dict.fromkeys(hashes)) if valid else None
