"""Verify project-restart history and operate its final-attempt lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from final_attempt_lifecycle import (
    LifecycleError,
    abandon_final_attempt,
    begin_final_attempt,
    bind_final_gate_output,
    load_bound_operations,
    reopen_seal,
    seal_attempt,
    validate_current_pointer,
    validate_seal,
    write_bound_final_gate_output,
)
from operation_ledger_format import JSONValue
from operation_ledger_writer import evidence_writer_lock
from plan_history_contract import (
    PlanContract,
    PlanHistoryError,
    load_contract,
    read_git_history,
    validate_history,
    validate_plan_identity,
)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-plan-sha", required=True)
    parser.add_argument("--expected-review-round", required=True)
    parser.add_argument("--plan-contract", type=Path, required=True)
    parser.add_argument("--operations", type=Path, required=True)


def _lifecycle(parser: argparse.ArgumentParser) -> None:
    _common(parser)
    parser.add_argument("--current-pointer", type=Path, required=True)
    parser.add_argument("--journal-root", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    _common(verify)
    verify.add_argument("--base", required=True)
    verify.add_argument("--plan-receipt", type=Path, required=True)
    verify.add_argument("--operations-seal", type=Path, required=True)
    verify.add_argument("--receipt-root", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    begin = commands.add_parser("begin-final-attempt")
    _lifecycle(begin)
    begin.add_argument("--head", required=True)
    begin.add_argument("--attempt-root", type=Path, required=True)
    abandon = commands.add_parser("abandon-final-attempt")
    _lifecycle(abandon)
    abandon.add_argument("--attempt-root", type=Path, required=True)
    abandon.add_argument("--failure-receipt", type=Path, required=True)
    seal = commands.add_parser("seal")
    _lifecycle(seal)
    seal.add_argument("--head", required=True)
    seal.add_argument("--attempt-root", type=Path)
    seal.add_argument("--output", type=Path, required=True)
    reopen = commands.add_parser("reopen")
    _lifecycle(reopen)
    reopen.add_argument("--attempt-root", type=Path, required=True)
    reopen.add_argument("--failure-receipt", type=Path, required=True)
    return parser


def _head(repo_root: Path) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _canonical_layout(repo_root: Path) -> dict[str, Path]:
    root = repo_root.absolute()
    receipt_root = root / ".omo/evidence/project-restart-consolidated-roadmap"
    return {
        "contract": root / "scripts/project-restart-plan-contract.json",
        "operations": receipt_root / "operations.ndjson",
        "plan_receipt": receipt_root / "precondition-baseline.json",
        "receipt_root": receipt_root,
        "pointer": receipt_root / "current-final-attempt.json",
        "journal_root": receipt_root / "lifecycle-journals",
        "attempt_root": receipt_root / "final-attempts",
    }


def _reject_symlink_ancestors(repo_root: Path, path: Path, label: str) -> None:
    try:
        relative = path.relative_to(repo_root.absolute())
    except ValueError as error:
        raise PlanHistoryError(f"{label} is outside the canonical repository layout") from error
    current = repo_root.absolute()
    for part in relative.parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise PlanHistoryError(f"{label} has a symlink ancestor")


def _pin_path(value: Path, expected: Path, label: str, repo_root: Path) -> None:
    if value.is_absolute():
        raise PlanHistoryError(f"{label} must use the exact repository-relative path")
    exact_relative = Path(os.path.relpath(expected, Path.cwd()))
    if value != exact_relative:
        raise PlanHistoryError(f"{label} must use the exact repository-relative path")
    _reject_symlink_ancestors(repo_root, expected, label)


def _pin_attempt_child_shape(
    value: Path,
    attempt_root: Path,
    allowed_names: set[str],
    label: str,
) -> None:
    expected_parent = Path(os.path.relpath(attempt_root, Path.cwd()))
    if (
        value.is_absolute()
        or value.name not in allowed_names
        or value.parent.parent != expected_parent
        or re.fullmatch(r"[0-9a-f]{64}", value.parent.name) is None
    ):
        raise PlanHistoryError(f"{label} must use the exact current-attempt path")


def _pin_cli_paths(
    args: argparse.Namespace, repo_root: Path
) -> tuple[dict[str, Path], dict[str, JSONValue] | None]:
    layout = _canonical_layout(repo_root)
    _pin_path(args.plan_contract, layout["contract"], "--plan-contract", repo_root)
    _pin_path(args.operations, layout["operations"], "--operations", repo_root)
    pointer: dict[str, JSONValue] | None = None
    if args.command == "verify":
        _pin_path(args.plan_receipt, layout["plan_receipt"], "--plan-receipt", repo_root)
        _pin_path(args.receipt_root, layout["receipt_root"], "--receipt-root", repo_root)
    else:
        _pin_path(args.current_pointer, layout["pointer"], "--current-pointer", repo_root)
        _pin_path(args.journal_root, layout["journal_root"], "--journal-root", repo_root)
        if args.attempt_root is not None:
            _pin_path(args.attempt_root, layout["attempt_root"], "--attempt-root", repo_root)
        elif args.command != "seal":
            raise PlanHistoryError("--attempt-root is required")
    if args.command == "seal":
        _pin_attempt_child_shape(
            args.output,
            layout["attempt_root"],
            {"operations-seal.json"},
            "--output",
        )
    elif args.command == "abandon-final-attempt":
        _pin_attempt_child_shape(
            args.failure_receipt,
            layout["attempt_root"],
            {"f2-failure.json", "f3-failure.json"},
            "--failure-receipt",
        )
    elif args.command == "reopen":
        _pin_attempt_child_shape(
            args.failure_receipt,
            layout["attempt_root"],
            {"f1-review.md", "f4-review.md"},
            "--failure-receipt",
        )
    elif args.command == "verify":
        _pin_attempt_child_shape(
            args.operations_seal,
            layout["attempt_root"],
            {"operations-seal.json"},
            "--operations-seal",
        )
        _pin_attempt_child_shape(
            args.output,
            layout["attempt_root"],
            {"f1-history.json", "f4-scope.json"},
            "--output",
        )
    for label, path in layout.items():
        _reject_symlink_ancestors(repo_root, path, f"canonical {label}")
    if args.command != "begin-final-attempt":
        pointer = validate_current_pointer(
            repo_root=repo_root,
            pointer_path=layout["pointer"],
            attempt_root=layout["attempt_root"],
        )
        attempt_dir = repo_root / str(pointer["attempt_dir"])
        if args.command == "seal":
            _pin_path(
                args.output,
                attempt_dir / "operations-seal.json",
                "--output",
                repo_root,
            )
        elif args.command in {"abandon-final-attempt", "reopen"}:
            _pin_path(
                args.failure_receipt,
                attempt_dir / args.failure_receipt.name,
                "--failure-receipt",
                repo_root,
            )
        else:
            _pin_path(
                args.operations_seal,
                attempt_dir / "operations-seal.json",
                "--operations-seal",
                repo_root,
            )
            _pin_path(args.output, attempt_dir / args.output.name, "--output", repo_root)
    return layout, pointer


def _identity(args: argparse.Namespace, layout: dict[str, Path]) -> PlanContract:
    contract = load_contract(layout["contract"])
    validate_plan_identity(
        contract,
        expected_plan_sha=args.expected_plan_sha,
        expected_review_round=args.expected_review_round,
        receipt_path=layout["plan_receipt"],
    )
    return contract


def _run(args: argparse.Namespace, repo_root: Path) -> dict[str, JSONValue]:
    layout, pinned_pointer = _pin_cli_paths(args, repo_root)
    contract = _identity(args, layout)
    attempt_dir = (
        repo_root / str(pinned_pointer["attempt_dir"])
        if pinned_pointer is not None and args.command != "verify"
        else None
    )
    common = {
        "repo_root": repo_root,
        "contract": contract,
        "plan_sha": args.expected_plan_sha,
        "review_round": args.expected_review_round,
        "operations": layout["operations"],
    }
    match args.command:
        case "begin-final-attempt":
            if args.head != _head(repo_root):
                raise LifecycleError("explicit begin HEAD disagrees with git HEAD")
            return begin_final_attempt(
                **common,
                head=args.head,
                attempt_root=layout["attempt_root"],
                pointer_path=layout["pointer"],
                journal_root=layout["journal_root"],
            )
        case "abandon-final-attempt":
            if attempt_dir is None:
                raise PlanHistoryError("abandon requires a current attempt")
            return abandon_final_attempt(
                **common,
                pointer_path=layout["pointer"],
                attempt_root=layout["attempt_root"],
                failure_receipt=attempt_dir / args.failure_receipt.name,
                journal_root=layout["journal_root"],
            )
        case "seal":
            if attempt_dir is None:
                raise PlanHistoryError("seal requires a current attempt")
            if args.head != _head(repo_root):
                raise LifecycleError("explicit seal HEAD disagrees with git HEAD")
            return seal_attempt(
                **common,
                head=args.head,
                pointer_path=layout["pointer"],
                output=attempt_dir / "operations-seal.json",
                journal_root=layout["journal_root"],
                attempt_root=layout["attempt_root"],
            )
        case "reopen":
            if attempt_dir is None:
                raise PlanHistoryError("reopen requires a current attempt")
            return reopen_seal(
                **common,
                pointer_path=layout["pointer"],
                attempt_root=layout["attempt_root"],
                failure_receipt=attempt_dir / args.failure_receipt.name,
                journal_root=layout["journal_root"],
            )
        case "verify":
            if args.base != contract.base_sha[:8]:
                raise PlanHistoryError("explicit base SHA disagrees with tracked contract")
            validate_plan_identity(
                contract,
                expected_plan_sha=args.expected_plan_sha,
                expected_review_round=args.expected_review_round,
                receipt_path=layout["plan_receipt"],
            )
            with evidence_writer_lock(layout["operations"]) as evidence_lock:
                pointer_path = layout["pointer"]
                pointer = validate_current_pointer(
                    repo_root=repo_root,
                    pointer_path=pointer_path,
                    attempt_root=layout["attempt_root"],
                )
                if pointer.get("status") != "sealed":
                    raise PlanHistoryError("history verification requires a sealed current attempt")
                attempt_dir = repo_root / str(pointer["attempt_dir"])
                _pin_path(
                    args.operations_seal,
                    attempt_dir / "operations-seal.json",
                    "--operations-seal",
                    repo_root,
                )
                _pin_path(args.output, attempt_dir / args.output.name, "--output", repo_root)
                with bind_final_gate_output(evidence_lock, attempt_dir) as output_binding:
                    head = _head(repo_root)
                    validate_seal(
                        contract=contract,
                        operations=layout["operations"],
                        seal_path=attempt_dir / "operations-seal.json",
                        pointer_path=pointer_path,
                        expected_head=head,
                        expected_attempt_id=str(pointer.get("attempt_id")),
                        expected_plan_sha=args.expected_plan_sha,
                        expected_review_round=args.expected_review_round,
                    )
                    history = validate_history(
                        contract,
                        read_git_history(repo_root, args.base, head),
                        load_bound_operations(layout["operations"], evidence_lock),
                    )
                    payload: dict[str, JSONValue] = {
                        "schema_version": 1,
                        "status": "APPROVE",
                        "head": head,
                        "attempt_id": str(pointer["attempt_id"]),
                        "plan_sha256": contract.plan_sha256,
                        "review_round": contract.review_round,
                        "history": history,
                    }
                    write_bound_final_gate_output(
                        output_binding,
                        attempt_dir / args.output.name,
                        payload,
                    )
                    return payload
        case unreachable:
            raise PlanHistoryError(f"unsupported command: {unreachable}")


def main() -> int:
    args = _parser().parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    try:
        result = _run(args, repo_root)
    except (
        LifecycleError,
        PlanHistoryError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        print(f"plan history rejected: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
