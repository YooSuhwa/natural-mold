"""Failure-tolerant receipt publication for isolated E2E finalization."""

from __future__ import annotations

from collections.abc import Callable

from e2e_runner_cleanup import publish_runner_receipts
from e2e_runner_contract import Lane, Project
from e2e_runner_export import ExportReceipt, export_artifacts
from e2e_runner_playwright import PlaywrightOutcome
from e2e_runner_runtime import E2eResources

type ExportAdapter = Callable[
    [E2eResources, Lane, Project, tuple[str, ...], tuple[PlaywrightOutcome, ...]],
    ExportReceipt,
]


def publish_and_export_artifacts(
    resources: E2eResources,
    lane: Lane,
    project: Project,
    secrets_to_scan: tuple[str, ...],
    failure_diagnostics: tuple[PlaywrightOutcome, ...],
    export_adapter: ExportAdapter = export_artifacts,
) -> tuple[bool, ExportReceipt]:
    receipts_published = publish_runner_receipts(resources, project)
    try:
        export = export_adapter(
            resources,
            lane,
            project,
            secrets_to_scan,
            failure_diagnostics,
        )
    except BaseException:  # noqa: BLE001 - finalizer must continue after adapter failure
        export = ExportReceipt(False, None, (), (), failure_code="export_adapter_exception")
    return receipts_published, export
