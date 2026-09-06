#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run append-operation.py --help
# 3. Or make executable and run:
#      chmod +x append-operation.py && ./append-operation.py --help
# ──────────────────

"""Create and append a bounded, hash-chained operations ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from operation_ledger_chain import verify_ledger
from operation_ledger_format import (
    InjectedInterruption,
    JSONValue,
    LedgerError,
    canonical_line,
)
from operation_ledger_genesis import build_genesis
from operation_ledger_writer import append_operation, write_genesis

__all__ = (
    "InjectedInterruption",
    "LedgerError",
    "append_operation",
    "build_genesis",
    "canonical_line",
    "verify_ledger",
    "write_genesis",
)


def _fingerprint_target(
    repo_root: Path, logical_path: str, target_kind: str
) -> dict[str, JSONValue]:
    path = repo_root / logical_path
    if not path.is_symlink():
        raise LedgerError("required bootstrap link is not symbolic")
    target = path.resolve(strict=True)
    if (target_kind == "file" and not target.is_file()) or (
        target_kind == "directory" and not target.is_dir()
    ):
        raise LedgerError("required bootstrap link target has the wrong kind")
    normalized = unicodedata.normalize("NFC", target.as_posix()).encode()
    return {
        "logical_path": logical_path,
        "link_kind": "symlink",
        "target_kind": target_kind,
        "target_path_sha256": hashlib.sha256(normalized).hexdigest(),
    }


def _bootstrap_facts(args: argparse.Namespace) -> dict[str, JSONValue]:
    repo_root = Path(__file__).resolve().parent.parent
    source_plan = Path(args.source_plan)
    if not source_plan.is_absolute() or not source_plan.is_file():
        raise LedgerError("source plan must be an existing absolute file")
    source_hash = hashlib.sha256(source_plan.read_bytes()).hexdigest()
    if source_hash != args.expected_plan_sha:
        raise LedgerError("source plan does not match the approved digest")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607 - repository-local bootstrap
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],  # noqa: S607 - repository-local bootstrap
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0 or head.stdout.strip() != args.base_sha:
        raise LedgerError("worktree HEAD does not match the approved base")
    if status.returncode != 0 or status.stdout != "":
        raise LedgerError("tracked worktree must be clean")
    return {
        "base_sha": args.base_sha,
        "tracked_status": "clean",
        "expected_plan_sha": args.expected_plan_sha,
        "expected_review_round": args.expected_review_round,
        "source_plan_sha256": source_hash,
        "resolved_links": [
            _fingerprint_target(repo_root, "backend/.env", "file"),
            _fingerprint_target(repo_root, "backend/data", "directory"),
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap")
    bootstrap.add_argument("--operations", required=True)
    bootstrap.add_argument("--base-sha", required=True)
    bootstrap.add_argument("--expected-plan-sha", required=True)
    bootstrap.add_argument("--expected-review-round", required=True)
    bootstrap.add_argument("--source-plan", required=True)
    append = subparsers.add_parser("append")
    append.add_argument("--operations", required=True)
    append.add_argument("--task-id", required=True)
    append.add_argument("--action-class", required=True)
    append.add_argument("--status", required=True)
    append.add_argument("--arguments-json", required=True)
    append.add_argument("--expected-previous-hash")
    return parser


def main() -> int:
    """Run the CLI boundary without printing paths or argument values."""
    args = _parser().parse_args()
    try:
        if args.command == "bootstrap":
            facts = _bootstrap_facts(args)
            entry = build_genesis(facts, clock=lambda: datetime.now(UTC))
            write_genesis(Path(args.operations).absolute(), entry)
        else:
            parsed = json.loads(args.arguments_json)
            if not isinstance(parsed, dict):
                raise LedgerError("operation arguments must be an object")
            entry = append_operation(
                Path(args.operations).absolute(),
                task_id=args.task_id,
                action_class=args.action_class,
                arguments=parsed,
                status=args.status,
                expected_previous_hash=args.expected_previous_hash,
            )
    except (LedgerError, OSError, json.JSONDecodeError) as error:
        print(f"operation ledger rejected: {type(error).__name__}", file=sys.stderr)
        return 2
    print(f"sequence={entry['sequence']} hash={entry['entry_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
