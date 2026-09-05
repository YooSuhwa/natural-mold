"""Versioned redacted receipt construction for isolated E2E runs."""

from __future__ import annotations

from dataclasses import dataclass

from e2e_failure_diagnostics import (
    FailureDiagnostic,
    failure_diagnostics_payload,
    source_rejection_payload,
)
from e2e_runner_contract import FinalE2eRun, Lane, Project
from e2e_runner_export import ExportReceipt

FINAL_E2E_TOP_KEYS = frozenset(
    {
        "schema_version",
        "runner",
        "lane",
        "project",
        "workers",
        "retries",
        "reuse_existing_server",
        "status",
        "failure_reason",
        "child_exit_code",
        "self_test",
        "run_id",
        "head_sha",
        "requested_specs",
        "skipped_ids",
        "attempt_id",
        "export_directory_absolute",
        "export_tree_sha256",
        "screenshots",
        "owned_run_root",
        "owned_database",
        "owned_backend",
        "owned_frontend",
        "owned_proxy",
        "postgres_image",
        "server_version_num",
        "alembic_head",
        "alembic_current",
        "schema_fingerprint",
        "second_upgrade_idempotent",
        "frontend_port",
        "backend_port",
        "selected_ids",
        "executed_ids",
        "unexpected_failures",
        "export",
        "egress",
        "cleanup",
    }
)
FINAL_E2E_EXPORT_KEYS = frozenset(
    {
        "schema_version",
        "secret_scan_passed",
        "failure_code",
        "export_directory",
        "attempt_id",
        "export_directory_absolute",
        "export_tree_sha256",
        "screenshots_absolute",
        "manifest",
        "files",
        "screenshots",
        "source_rejection",
    }
)


@dataclass(frozen=True, slots=True)
class RunFacts:
    run_id: str
    server_version_num: str | None
    alembic_head: str | None
    alembic_current: str | None
    schema_fingerprint: str | None
    second_upgrade_idempotent: bool


def build_manifest(
    *,
    lane: Lane,
    project: Project,
    status: str,
    failure_reason: str | None,
    child_exit_code: int,
    self_test: str,
    selected_ids: tuple[str, ...],
    executed_ids: tuple[str, ...],
    skipped_ids: tuple[str, ...] = (),
    unexpected_failures: tuple[FailureDiagnostic, ...],
    facts: RunFacts,
    export: ExportReceipt,
    egress: dict[str, object],
    ownership: dict[str, bool],
    cleanup: dict[str, bool | None],
    final_run: FinalE2eRun | None = None,
) -> dict[str, object]:
    """Return only logical ownership facts; physical run-root paths are forbidden."""
    return {
        "schema_version": 1,
        "runner": "moldy-isolated-e2e",
        "lane": lane,
        "project": project,
        "workers": 1,
        "retries": 0,
        "reuse_existing_server": False,
        "status": status,
        "failure_reason": failure_reason,
        "child_exit_code": child_exit_code,
        "self_test": self_test,
        "run_id": facts.run_id,
        **(
            {
                "head_sha": final_run.head_sha,
                "requested_specs": list(final_run.requested_specs),
                "skipped_ids": list(skipped_ids),
            }
            if final_run is not None
            else {}
        ),
        **(
            {
                "attempt_id": export.attempt_id,
                "export_directory_absolute": export.export_directory_absolute,
                "export_tree_sha256": export.export_tree_sha256,
                "screenshots": list(export.screenshots_absolute),
            }
            if export.export_directory_absolute is not None
            else {}
        ),
        "owned_run_root": ownership["run_root"],
        "owned_database": ownership["database"],
        "owned_backend": ownership["backend"],
        "owned_frontend": ownership["frontend"],
        "owned_proxy": ownership["proxy"],
        "postgres_image": "postgres:16-alpine",
        "server_version_num": facts.server_version_num,
        "alembic_head": facts.alembic_head,
        "alembic_current": facts.alembic_current,
        "schema_fingerprint": facts.schema_fingerprint,
        "second_upgrade_idempotent": facts.second_upgrade_idempotent,
        "frontend_port": 3100 if lane == "scripted" else 3200,
        "backend_port": 8101 if lane == "scripted" else 8201,
        "selected_ids": list(selected_ids),
        "executed_ids": list(executed_ids),
        "unexpected_failures": failure_diagnostics_payload(unexpected_failures, project),
        "export": {
            "schema_version": export.schema_version,
            "secret_scan_passed": export.secret_scan_passed,
            "failure_code": export.failure_code,
            "export_directory": export.export_directory,
            **(
                {
                    "attempt_id": export.attempt_id,
                    "export_directory_absolute": export.export_directory_absolute,
                    "export_tree_sha256": export.export_tree_sha256,
                    "screenshots_absolute": list(export.screenshots_absolute),
                }
                if export.export_directory_absolute is not None
                else {}
            ),
            "manifest": (
                {
                    "path": export.manifest.path,
                    "sha256": export.manifest.sha256,
                    "size_bytes": export.manifest.size_bytes,
                }
                if export.manifest is not None
                else None
            ),
            "files": [
                {
                    "path": file.path,
                    "sha256": file.sha256,
                    "size_bytes": file.size_bytes,
                }
                for file in export.files
            ],
            "screenshots": list(export.screenshots),
            "source_rejection": (
                source_rejection_payload(export.source_rejection)
                if export.source_rejection is not None
                else None
            ),
        },
        "egress": egress,
        "cleanup": cleanup,
    }
