"""Summarize the complete paired worker trial without accepting a model's own completion claim."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, NonNegativeInt

from evals.continuous_work.cases import SCENARIOS, meets_task, task_text


class Version(BaseModel):
    run_id: UUID
    sha256: str | None
    csv_text: str | None
    version_id: UUID | None = None
    model_declared_done: bool


class PinnedResult(BaseModel):
    run_id: UUID
    version_id: UUID
    sha256: str
    csv_text: str


class Attempt(BaseModel):
    run_id: UUID
    passed: bool | None
    version_sha256: str | None


class Call(BaseModel):
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt


class Observation(BaseModel):
    case: str
    arm: Literal["ordinary", "goal"]
    task_sha256: str
    run_ids: list[UUID]
    followups_automatic: NonNegativeInt
    followups_manual: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    projected_cost_usd: Decimal | None
    goal_status: str | None
    error: str | None
    versions: list[Version]
    pinned_goal_result: PinnedResult | None
    goal_attempts: list[Attempt]
    provider_calls: list[Call]
    usage_rows: NonNegativeInt
    history_visible_completions: NonNegativeInt
    provider_usage_matches_ledger: bool


def score(observation: Observation) -> dict[str, object]:
    o = observation
    if o.task_sha256 != hashlib.sha256(task_text().encode()).hexdigest():
        raise ValueError(f"changed starting prompt in {o.case}/{o.arm}")
    if len(set(o.run_ids)) != len(o.run_ids) or [v.run_id for v in o.versions] != o.run_ids:
        raise ValueError("missing, reordered or repeated run observations")
    if o.usage_rows != len(o.run_ids) or not o.provider_usage_matches_ledger:
        raise ValueError("missing stored usage rows")
    if [
        sum(c.input_tokens for c in o.provider_calls),
        sum(c.output_tokens for c in o.provider_calls),
    ] != [o.input_tokens, o.output_tokens]:
        raise ValueError("stored worker usage and fixture response usage disagree")
    if o.error is None and o.history_visible_completions != len(o.run_ids):
        raise ValueError("model completion claims missing from persisted chat history")
    if o.projected_cost_usd is not None and o.projected_cost_usd != (
        Decimal(o.input_tokens + 2 * o.output_tokens) / 1_000_000
    ):
        raise ValueError("ledger estimate does not match the synthetic catalog tariff")
    if any(a.run_id not in o.run_ids for a in o.goal_attempts):
        raise ValueError("goal evidence refers to an unrelated run")
    accepted = {(r.run_id, r.version_sha256) for r in o.goal_attempts if r.passed}
    pinned = o.pinned_goal_result
    latest = None
    correct_approved = False
    for version in o.versions:
        if version.csv_text is None:
            if version.sha256 is not None:
                raise ValueError("digest reported without downloaded bytes")
            continue
        if hashlib.sha256(version.csv_text.encode()).hexdigest() != version.sha256:
            raise ValueError("downloaded artifact digest does not match")
        latest = version.csv_text
        if (
            pinned
            and (version.run_id, version.sha256) in accepted
            and (version.run_id, version.version_id, version.sha256, version.csv_text)
            == (pinned.run_id, pinned.version_id, pinned.sha256, pinned.csv_text)
            and meets_task(pinned.csv_text)
        ):
            correct_approved = True
    passed = meets_task(latest)
    marked_complete = o.goal_status == "completed"
    falsely_completed = marked_complete and not (correct_approved and passed)
    return {
        "case": o.case,
        "arm": o.arm,
        "runs": len(o.run_ids),
        "download_meets_user_task": passed,
        "goal_marked_complete": marked_complete if o.arm == "goal" else None,
        "false_goal_completion": falsely_completed if o.arm == "goal" else None,
        "last_model_declared_done": o.versions[-1].model_declared_done if o.versions else False,
        "manual_followups": o.followups_manual,
        "automatic_followups": o.followups_automatic,
        "input_tokens": o.input_tokens,
        "output_tokens": o.output_tokens,
        "projected_cost_usd": str(o.projected_cost_usd)
        if o.projected_cost_usd is not None
        else None,
        "error": o.error,
    }


def aggregate(entries: list[Observation]) -> dict[str, object]:
    expected = {(s.slug, arm) for s in SCENARIOS for arm in ("ordinary", "goal")}
    if len(entries) != len(expected) or {(o.case, o.arm) for o in entries} != expected:
        raise ValueError("one observation in both arms is required for every corpus case")
    cases = [score(o) for o in entries]
    totals = {}
    for arm in ("ordinary", "goal"):
        group = [o for o in entries if o.arm == arm]
        scored = [row for row in cases if row["arm"] == arm]
        costs = [o.projected_cost_usd for o in group if o.projected_cost_usd is not None]
        totals[arm] = {
            "assigned": len(group),
            "artifacts_passing_independent_review": sum(
                r["download_meets_user_task"] is True for r in scored
            ),
            "registered_goal_completions": sum(o.goal_status == "completed" for o in group)
            if arm == "goal"
            else None,
            "falsely_completed": sum(r["false_goal_completion"] is True for r in scored)
            if arm == "goal"
            else None,
            "unmet_tasks": sum(r["download_meets_user_task"] is False for r in scored),
            "run_errors": sum(bool(o.error) for o in group),
            "blocked_goals": sum(o.goal_status == "blocked" for o in group)
            if arm == "goal"
            else None,
            "manual_followups": sum(o.followups_manual for o in group),
            "automatic_followups": sum(o.followups_automatic for o in group),
            "rounds": sum(len(o.run_ids) for o in group),
            "provider_calls": sum(len(o.provider_calls) for o in group),
            "input_tokens": sum(o.input_tokens for o in group),
            "output_tokens": sum(o.output_tokens for o in group),
            "projected_cost_usd": str(sum(costs, Decimal(0))) if len(costs) == len(group) else None,
        }
    return {"totals": totals, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    entries = [
        Observation.model_validate_json(line)
        for line in args.observations.read_text(encoding="utf-8").splitlines()
    ]
    report = aggregate(entries)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
