"""Trusted command-line and manifest boundary for the isolated E2E runner."""

from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path
from typing import Protocol

from e2e_runner_contract import (
    DEFAULT_PROJECT,
    E2eContractError,
    FinalE2eRun,
    Lane,
    Project,
    SelfTest,
    parse_final_e2e_run,
    parse_lane,
    parse_project,
    parse_self_test,
    validate_forwarded_arguments,
)
from e2e_runner_runtime import REPO_ROOT
from postgres_manifest_io import (
    close_evidence_directory,
    on_signal,
    open_evidence_directory,
    open_manifest_directory,
    verify_evidence_directory,
    write_manifest,
)
from postgres_runner_contract import ensure_evidence_root, validate_manifest_destination


class E2eScenarioRunner(Protocol):
    def __call__(
        self,
        lane: Lane,
        project: Project,
        arguments: tuple[str, ...],
        self_test: SelfTest,
        final_run: FinalE2eRun | None,
    ) -> tuple[dict[str, object], int]: ...


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("lane")
    parser.add_argument("--project")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--self-test",
        default="normal",
        choices=("normal", "spec-failure", "server-failure", "dsn-failure", "sigint"),
    )
    return parser


def run_cli(run_e2e: E2eScenarioRunner) -> int:
    arguments, unknown = _parser().parse_known_args()
    lane = parse_lane(arguments.lane)
    project = parse_project(arguments.project or DEFAULT_PROJECT[lane], lane)
    forwarded = tuple(unknown)
    if forwarded[:1] == ("--",):
        forwarded = forwarded[1:]
    forwarded = validate_forwarded_arguments(forwarded, lane)
    self_test = parse_self_test(arguments.self_test)
    evidence_root = ensure_evidence_root(REPO_ROOT)
    destination = (
        arguments.manifest if arguments.manifest.is_absolute() else REPO_ROOT / arguments.manifest
    )
    if destination.absolute().parent == evidence_root.absolute():
        validate_manifest_destination(destination, evidence_root)
        evidence = open_evidence_directory(evidence_root)
    else:
        evidence = open_manifest_directory(destination, evidence_root, REPO_ROOT)
    try:
        final_run = parse_final_e2e_run(
            destination,
            lane=lane,
            project=project,
            requested_specs=forwarded,
            export_slug=os.environ.get("E2E_EXPORT_SLUG"),
            attempt_id=evidence.attempt_id,
            head_sha=evidence.head_sha,
        )
    except E2eContractError:
        close_evidence_directory(evidence)
        raise
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        verify_evidence_directory(evidence)
        manifest, exit_code = run_e2e(lane, project, forwarded, self_test, final_run)
        write_manifest(destination, manifest, evidence)
        return exit_code
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        close_evidence_directory(evidence)
