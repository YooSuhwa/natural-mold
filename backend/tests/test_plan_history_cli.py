from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import check_plan_history as history_cli
import final_attempt_io as io_module
import pytest
from check_plan_history import _parser
from final_attempt_io import LifecycleError
from operation_ledger_format import JSONValue
from plan_history_contract import PlanHistoryError, load_contract
from plan_history_support import (
    CONTRACT_PATH,
    HEAD,
    PLAN_SHA,
    REVIEW_ROUND,
    _begin,
    _cli_path,
    _seal,
    lifecycle_arguments,
)


@pytest.fixture
def lifecycle(tmp_path: Path) -> dict[str, object]:
    return lifecycle_arguments(tmp_path)


def test_seal_parser_requires_attempt_root() -> None:
    parser = _parser()
    option_strings = {
        option
        for action in parser._subparsers._group_actions  # noqa: SLF001
        for choice in action.choices.values()
        if choice.prog.endswith(" seal")
        for item in choice._actions  # noqa: SLF001
        for option in item.option_strings
    }
    assert "--attempt-root" in option_strings


def test_seal_cli_accepts_the_exact_roadmap_shape_without_unplanned_arguments() -> None:
    args = _parser().parse_args(
        [
            "seal",
            "--expected-plan-sha",
            PLAN_SHA,
            "--expected-review-round",
            REVIEW_ROUND,
            "--plan-contract",
            "../scripts/project-restart-plan-contract.json",
            "--operations",
            "../.omo/evidence/project-restart-consolidated-roadmap/operations.ndjson",
            "--head",
            HEAD,
            "--current-pointer",
            "../.omo/evidence/project-restart-consolidated-roadmap/current-final-attempt.json",
            "--output",
            "../.omo/evidence/project-restart-consolidated-roadmap/final-attempts/id/operations-seal.json",
            "--journal-root",
            "../.omo/evidence/project-restart-consolidated-roadmap/lifecycle-journals",
        ]
    )

    assert args.command == "seal"


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("begin-final-attempt", "begin_final_attempt"),
        ("abandon-final-attempt", "abandon_final_attempt"),
        ("seal", "seal_attempt"),
        ("reopen", "reopen_seal"),
    ],
)
def test_lifecycle_cli_run_dispatches_every_command_with_attempt_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str, target: str
) -> None:
    evidence = tmp_path / ".omo/evidence/project-restart-consolidated-roadmap"
    attempt_root = evidence / "final-attempts"
    attempt_id = "a" * 64
    attempt_dir = attempt_root / attempt_id
    common = [
        command,
        "--expected-plan-sha",
        PLAN_SHA,
        "--expected-review-round",
        REVIEW_ROUND,
        "--plan-contract",
        _cli_path(tmp_path / "scripts/project-restart-plan-contract.json"),
        "--operations",
        _cli_path(evidence / "operations.ndjson"),
        "--current-pointer",
        _cli_path(evidence / "current-final-attempt.json"),
        "--journal-root",
        _cli_path(evidence / "lifecycle-journals"),
    ]
    if command != "seal":
        common.extend(("--attempt-root", _cli_path(attempt_root)))
    if command in {"begin-final-attempt", "seal"}:
        common.extend(("--head", HEAD))
    if command in {"abandon-final-attempt", "reopen"}:
        failure_name = "f2-failure.json" if command == "abandon-final-attempt" else "f1-review.md"
        common.extend(("--failure-receipt", _cli_path(attempt_dir / failure_name)))
    if command == "seal":
        common.extend(("--output", _cli_path(attempt_dir / "operations-seal.json")))
    args = _parser().parse_args(common)
    captured: dict[str, object] = {}

    monkeypatch.setattr(history_cli, "_identity", lambda *_: load_contract(CONTRACT_PATH))
    monkeypatch.setattr(history_cli, "_head", lambda _: HEAD)
    monkeypatch.setattr(
        history_cli,
        "validate_current_pointer",
        lambda **_: {
            "attempt_id": attempt_id,
            "attempt_dir": attempt_dir.relative_to(tmp_path).as_posix(),
            "status": "open" if command != "reopen" else "sealed",
            "head": HEAD,
        },
    )

    def invoke(**kwargs: object) -> dict[str, JSONValue]:
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(history_cli, target, invoke)

    assert history_cli._run(args, tmp_path) == {"status": "ok"}
    assert captured["attempt_root"] == attempt_root


def test_plan_history_cli_rejects_caller_selected_trust_anchors(tmp_path: Path) -> None:
    args = _parser().parse_args(
        [
            "begin-final-attempt",
            "--expected-plan-sha",
            PLAN_SHA,
            "--expected-review-round",
            REVIEW_ROUND,
            "--plan-contract",
            str(tmp_path / "forged-contract.json"),
            "--operations",
            str(tmp_path / "forged-operations.ndjson"),
            "--current-pointer",
            str(tmp_path / "pointer.json"),
            "--journal-root",
            str(tmp_path / "journals"),
            "--attempt-root",
            str(tmp_path / "attempts"),
            "--head",
            HEAD,
        ]
    )

    with pytest.raises(PlanHistoryError, match="exact repository-relative path"):
        history_cli._run(args, tmp_path)


def _exact_cli_args(
    repo_root: Path,
    command: str,
    *,
    attempt_id: str = "a" * 64,
    verify_output: str = "f1-history.json",
) -> list[str]:
    evidence = repo_root / ".omo/evidence/project-restart-consolidated-roadmap"
    attempt_dir = evidence / "final-attempts" / attempt_id
    common = [
        command,
        "--expected-plan-sha",
        PLAN_SHA,
        "--expected-review-round",
        REVIEW_ROUND,
        "--plan-contract",
        _cli_path(repo_root / "scripts/project-restart-plan-contract.json"),
        "--operations",
        _cli_path(evidence / "operations.ndjson"),
    ]
    if command == "verify":
        return [
            *common,
            "--base",
            "7a9cee88",
            "--plan-receipt",
            _cli_path(evidence / "precondition-baseline.json"),
            "--operations-seal",
            _cli_path(attempt_dir / "operations-seal.json"),
            "--receipt-root",
            _cli_path(evidence),
            "--output",
            _cli_path(attempt_dir / verify_output),
        ]
    common.extend(
        (
            "--current-pointer",
            _cli_path(evidence / "current-final-attempt.json"),
            "--journal-root",
            _cli_path(evidence / "lifecycle-journals"),
        )
    )
    if command != "seal":
        common.extend(("--attempt-root", _cli_path(evidence / "final-attempts")))
    if command in {"begin-final-attempt", "seal"}:
        common.extend(("--head", HEAD))
    if command == "abandon-final-attempt":
        common.extend(("--failure-receipt", _cli_path(attempt_dir / "f2-failure.json")))
    elif command == "reopen":
        common.extend(("--failure-receipt", _cli_path(attempt_dir / "f1-review.md")))
    elif command == "seal":
        common.extend(("--output", _cli_path(attempt_dir / "operations-seal.json")))
    return common


@pytest.mark.parametrize(
    ("command", "option"),
    [
        ("begin-final-attempt", "--plan-contract"),
        ("begin-final-attempt", "--operations"),
        ("begin-final-attempt", "--current-pointer"),
        ("begin-final-attempt", "--journal-root"),
        ("begin-final-attempt", "--attempt-root"),
        ("abandon-final-attempt", "--failure-receipt"),
        ("seal", "--output"),
        ("verify", "--plan-receipt"),
        ("verify", "--receipt-root"),
        ("verify", "--operations-seal"),
        ("verify", "--output"),
    ],
)
@pytest.mark.parametrize("path_kind", ["absolute-external", "wrong-relative"])
def test_cli_rejects_every_alternate_evidence_path_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
    option: str,
    path_kind: str,
) -> None:
    args_list = _exact_cli_args(tmp_path, command)
    wrong = tmp_path / "external" / Path(option).name
    args_list[args_list.index(option) + 1] = (
        str(wrong) if path_kind == "absolute-external" else _cli_path(wrong)
    )
    args = _parser().parse_args(args_list)
    called = False

    def unexpected(**_: object) -> dict[str, JSONValue]:
        nonlocal called
        called = True
        return {}

    for target in ("begin_final_attempt", "abandon_final_attempt", "seal_attempt", "reopen_seal"):
        monkeypatch.setattr(history_cli, target, unexpected)
    monkeypatch.setattr(history_cli, "_identity", lambda *_: load_contract(CONTRACT_PATH))
    monkeypatch.setattr(
        history_cli,
        "validate_current_pointer",
        lambda **_: pytest.fail("pointer opened before canonical path rejection"),
    )

    with pytest.raises(PlanHistoryError, match=r"exact (repository-relative|current-attempt) path"):
        history_cli._run(args, tmp_path)
    assert called is False


def test_cli_rejects_dotdot_alias_of_an_exact_path(tmp_path: Path) -> None:
    args_list = _exact_cli_args(tmp_path, "begin-final-attempt")
    contract_index = args_list.index("--plan-contract") + 1
    exact = Path(args_list[contract_index])
    args_list[contract_index] = str(exact.parent / "ignored" / ".." / exact.name)

    with pytest.raises(PlanHistoryError, match="exact repository-relative path"):
        history_cli._run(_parser().parse_args(args_list), tmp_path)


def test_cli_rejects_symlinked_canonical_evidence_ancestor(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    evidence_parent = tmp_path / ".omo/evidence"
    evidence_parent.mkdir(parents=True)
    (evidence_parent / "project-restart-consolidated-roadmap").symlink_to(
        external,
        target_is_directory=True,
    )

    with pytest.raises(PlanHistoryError, match="symlink ancestor"):
        history_cli._run(
            _parser().parse_args(_exact_cli_args(tmp_path, "begin-final-attempt")),
            tmp_path,
        )


def _prepare_verify_run(
    lifecycle: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    output_name: str,
) -> tuple[argparse.Namespace, Path, Path]:
    pointer = _begin(lifecycle)
    repo_root = Path(lifecycle["repo_root"])
    attempt_dir = repo_root / str(pointer["attempt_dir"])
    _seal(lifecycle, attempt_dir / "operations-seal.json")
    monkeypatch.setattr(history_cli, "_identity", lambda *_: load_contract(CONTRACT_PATH))
    monkeypatch.setattr(history_cli, "validate_plan_identity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(history_cli, "_head", lambda _: HEAD)
    monkeypatch.setattr(history_cli, "read_git_history", lambda *_: ())
    monkeypatch.setattr(
        history_cli,
        "validate_history",
        lambda *_: {"primary_sequence": ["02", "01"]},
    )
    args = _parser().parse_args(
        _exact_cli_args(
            repo_root,
            "verify",
            attempt_id=str(pointer["attempt_id"]),
            verify_output=output_name,
        )
    )
    return args, repo_root, attempt_dir


@pytest.mark.parametrize("output_name", ["f1-history.json", "f4-scope.json"])
def test_verify_cli_writes_through_bound_current_attempt_directory(
    lifecycle: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    output_name: str,
) -> None:
    args, repo_root, attempt_dir = _prepare_verify_run(lifecycle, monkeypatch, output_name)

    result = history_cli._run(args, repo_root)

    assert result["status"] == "APPROVE"
    assert json.loads((attempt_dir / output_name).read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("output_name", ["f1-history.json", "f4-scope.json"])
@pytest.mark.parametrize("substitution", ["directory", "symlink"])
@pytest.mark.parametrize("replacement_check", [3, 4, 5])
def test_verify_cli_rejects_attempt_directory_replacement_after_binding(
    lifecycle: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    output_name: str,
    substitution: str,
    replacement_check: int,
) -> None:
    args, repo_root, attempt_dir = _prepare_verify_run(lifecycle, monkeypatch, output_name)
    saved = attempt_dir.with_name(attempt_dir.name + "-saved")
    attacker = tmp_path / "attacker-attempt"
    real_revalidate = io_module._revalidate_parent
    checks = 0

    def replace_after_temp_write(path: Path, descriptor: int, expected: os.stat_result) -> None:
        nonlocal checks
        checks += 1
        if checks == replacement_check:
            attempt_dir.rename(saved)
            if substitution == "directory":
                attempt_dir.mkdir(mode=0o700)
                attacker_path = attempt_dir
            else:
                attacker.mkdir(mode=0o700)
                attempt_dir.symlink_to(attacker, target_is_directory=True)
                attacker_path = attacker
            assert not (attacker_path / output_name).exists()
        real_revalidate(path, descriptor, expected)

    monkeypatch.setattr(io_module, "_revalidate_parent", replace_after_temp_write)

    with pytest.raises(LifecycleError, match="parent path changed|parent identity changed"):
        history_cli._run(args, repo_root)

    assert not (saved / output_name).exists()
    assert not (attacker / output_name).exists()
    if substitution == "directory":
        assert not (attempt_dir / output_name).exists()


def test_bound_final_gate_writer_rejects_zero_progress_write(
    lifecycle: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    args, repo_root, attempt_dir = _prepare_verify_run(
        lifecycle,
        monkeypatch,
        "f1-history.json",
    )
    monkeypatch.setattr(io_module.os, "write", lambda *_: 0)

    with pytest.raises(LifecycleError, match="made no progress"):
        history_cli._run(args, repo_root)

    assert not (attempt_dir / "f1-history.json").exists()
