"""Execution-receipt projection for isolated Playwright runs."""

from __future__ import annotations

from pathlib import Path

from e2e_runner_playwright import (
    PlaywrightOutcome,
    PlaywrightReceiptError,
    parse_playwright_execution_json,
)


def read_execution_receipt(
    path: Path, child_exit_code: int
) -> tuple[tuple[str, ...], tuple[PlaywrightOutcome, ...]]:
    try:
        report = parse_playwright_execution_json(path)
    except PlaywrightReceiptError:
        if child_exit_code == 0:
            raise
        return (), ()
    return tuple(node.node_id for node in report.nodes), report.unexpected_outcomes
