from __future__ import annotations

# ruff: noqa: E402
import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from plan_history_contract import (
    CommitRecord,
    PlanHistoryError,
    load_contract,
    validate_history,
    validate_plan_identity,
)
from plan_history_support import (
    CONTRACT_PATH,
    EVIDENCE,
    PLAN_SHA,
    REVIEW_ROUND,
    _complete_history,
    _write_json,
)


def test_actual_history_plus_remaining_primaries_satisfies_strict_contract() -> None:
    contract, commits, entries = _complete_history()

    result = validate_history(contract, commits, entries)

    assert result["primary_sequence"] == list(contract.primary_sequence)
    repairs = result["repairs"]
    assert isinstance(repairs, list)
    assert len(repairs) > 3
    legacy_commit_shas: set[str] = set()
    for repair in repairs:
        assert isinstance(repair, dict)
        if repair.get("legacy") is not True:
            continue
        commit_sha = repair.get("commit_sha")
        assert isinstance(commit_sha, str)
        legacy_commit_shas.add(commit_sha)
    assert legacy_commit_shas == {
        "ee624b6272140567672f29a184daa7abf869df0d",
        "1024beab13a914cd78c98cfa552c6a76cc9e3237",
        "3846b6b4e05fbed479fd9d0521bc9b757550cbd3",
    }


def test_contract_cannot_redefine_the_mandated_primary_order(tmp_path: Path) -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    payload["primary_sequence"][0:2] = ["01", "02"]
    contract_path = _write_json(tmp_path / "contract.json", payload)

    with pytest.raises(PlanHistoryError, match="immutable mandated order"):
        load_contract(contract_path)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "out-of-order"])
def test_primary_history_rejects_missing_duplicate_or_out_of_order(mutation: str) -> None:
    contract, commits_tuple, entries = _complete_history()
    commits = list(commits_tuple)
    primary_indexes = [
        index for index, commit in enumerate(commits) if "Plan-Item:" in commit.message
    ]
    if mutation == "missing":
        commits.pop(primary_indexes[-1])
    elif mutation == "duplicate":
        commits.insert(primary_indexes[-1], commits[primary_indexes[-1]])
    else:
        left, right = primary_indexes[-2:]
        commits[left], commits[right] = commits[right], commits[left]

    with pytest.raises(PlanHistoryError, match="primary Plan-Item sequence"):
        validate_history(contract, tuple(commits), entries)


@pytest.mark.parametrize(
    "message",
    [
        "repair without trailer\n",
        "repair\n\nPlan-Fix: 99\nInvalidates-Receipt: " + "a" * 64 + "\n",
        "repair\n\nPlan-Fix: 04\n",
        "repair\n\nPlan-Fix: 4\nInvalidates-Receipt: " + "a" * 64 + "\n",
        "repair\n\nPlan-Repair: 04\n",
    ],
)
def test_unmapped_or_invalid_repair_fails_closed(message: str) -> None:
    contract, commits_tuple, entries = _complete_history()
    commits = list(commits_tuple)
    commits.append(CommitRecord("9" * 40, (commits[-1].sha,), "8" * 40, message))

    with pytest.raises(PlanHistoryError):
        validate_history(contract, tuple(commits), entries)


def test_sha_bound_legacy_exception_rejects_altered_identity() -> None:
    contract, commits_tuple, entries = _complete_history()
    commits = list(commits_tuple)
    index = next(i for i, commit in enumerate(commits) if commit.sha.startswith("ee624b"))
    commits[index] = dataclasses.replace(commits[index], tree_sha="0" * 40)

    with pytest.raises(PlanHistoryError, match="legacy exception identity mismatch"):
        validate_history(contract, tuple(commits), entries)


def test_plan_identity_requires_tracked_explicit_and_receipt_agreement(tmp_path: Path) -> None:
    contract = load_contract(CONTRACT_PATH)
    receipt = json.loads((EVIDENCE / "precondition-baseline.json").read_text())
    receipt["source"]["plan_sha256"] = "0" * 64
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(PlanHistoryError, match="identities disagree"):
        validate_plan_identity(
            contract,
            expected_plan_sha=PLAN_SHA,
            expected_review_round=REVIEW_ROUND,
            receipt_path=receipt_path,
        )


@pytest.mark.parametrize("mutation", ["top-level-key", "negative-sequence", "fourth-exception"])
def test_plan_contract_rejects_any_unreviewed_schema_or_exception(
    tmp_path: Path, mutation: str
) -> None:
    raw = json.loads(CONTRACT_PATH.read_text())
    if mutation == "top-level-key":
        raw["extra"] = True
    elif mutation == "negative-sequence":
        raw["legacy_exceptions"][0]["ledger_bindings"][0]["sequence"] = -1
    else:
        raw["legacy_exceptions"].append(dict(raw["legacy_exceptions"][0]))
        raw["legacy_exceptions"][-1]["commit_sha"] = "9" * 40
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(PlanHistoryError):
        load_contract(path)


def test_history_rejects_commit_binding_with_failed_status_or_wrong_action() -> None:
    contract, commits, entries = _complete_history()
    target = next(commit for commit in commits if "Plan-Item: 22" in commit.message)
    for entry in entries:
        arguments = entry.get("arguments")
        if isinstance(arguments, dict) and arguments.get("commit") == target.sha:
            entry["status"] = "failed"
            entry["action_class"] = "observation"

    with pytest.raises(PlanHistoryError, match="receipt binding is missing"):
        validate_history(contract, commits, entries)
