"""Redacted manifest tests for the isolated E2E runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from e2e_failure_diagnostics import FailureDiagnostic, FailureLocation  # noqa: E402
from e2e_runner_contract import FinalE2eRun  # noqa: E402
from e2e_runner_export import ExportFile, ExportReceipt  # noqa: E402
from e2e_runner_failure_phase import FailurePhase  # noqa: E402
from e2e_runner_manifest import RunFacts, build_manifest  # noqa: E402
from e2e_runner_network_failure import NetworkFailureCode  # noqa: E402


def test_manifest_is_versioned_and_omits_physical_root_and_secrets() -> None:
    """Given logical facts, when serialized, then physical roots and secrets are absent."""
    manifest = build_manifest(
        lane="scripted",
        project="scripted-smoke",
        status="passed",
        failure_reason=None,
        child_exit_code=0,
        self_test="normal",
        selected_ids=("scripted-smoke::e2e/a.spec.ts::works",),
        executed_ids=("scripted-smoke::e2e/a.spec.ts::works",),
        unexpected_failures=(
            FailureDiagnostic(
                "scripted-smoke::e2e/a.spec.ts::works",
                "failed",
                FailureLocation("e2e/a.spec.ts", 7, 3),
                (
                    NetworkFailureCode.API_REQUEST_FAILURE,
                    NetworkFailureCode.OTHER_RESPONSE_FAILURE,
                ),
                FailurePhase.VERIFY_ERROR_COLLECTORS,
            ),
        ),
        facts=RunFacts("run-id", "160001", "m70", "m70", "hash", True),
        export=ExportReceipt(
            True,
            "output/e2e-captures/20260831-smoke",
            (ExportFile("junit.xml", "0" * 64, 10),),
            (),
        ),
        egress={"enabled": False, "clean_stop": True, "records": []},
        ownership={
            "run_root": True,
            "database": True,
            "backend": True,
            "frontend": True,
            "proxy": False,
        },
        cleanup={"cleanup_run_root_removed": True},
    )

    encoded = json.dumps(manifest)
    assert manifest["schema_version"] == 1
    assert "run_root" not in manifest
    assert "DATABASE_URL" not in encoded
    assert "upstream-api-key" not in encoded
    assert "/tmp/" not in encoded
    assert manifest["unexpected_failures"] == [
        {
            "node_id": "scripted-smoke::e2e/a.spec.ts::works",
            "status": "failed",
            "location": {"file": "e2e/a.spec.ts", "line": 7, "column": 3},
            "network_failure_codes": ["api_request_failure", "other_response_failure"],
            "failure_phase": "verify_error_collectors",
        }
    ]


def test_final_manifest_adds_head_request_and_preserved_skip_identities() -> None:
    """Given a final run, when serialized, then final-only selection evidence is explicit."""
    attempt_id = "a" * 64
    manifest = build_manifest(
        lane="scripted",
        project="scripted-full",
        status="passed",
        failure_reason=None,
        child_exit_code=0,
        self_test="normal",
        selected_ids=("scripted-full::e2e/a.spec.ts::active",),
        executed_ids=("scripted-full::e2e/a.spec.ts::active",),
        skipped_ids=("scripted-full::e2e/b.spec.ts::skipped",),
        unexpected_failures=(),
        facts=RunFacts("run-id", "160001", "m70", "m70", "hash", True),
        export=ExportReceipt(
            True,
            "output/e2e-captures/final",
            (),
            (),
            attempt_id=attempt_id,
            export_directory_absolute="/repo/output/e2e-captures/final",
            export_tree_sha256="c" * 64,
        ),
        egress={"enabled": False, "clean_stop": True, "records": []},
        ownership=dict.fromkeys(("run_root", "database", "backend", "frontend", "proxy"), True),
        cleanup={"cleanup_run_root_removed": True},
        final_run=FinalE2eRun(attempt_id, "b" * 40, ()),
    )

    assert manifest["head_sha"] == "b" * 40
    assert manifest["requested_specs"] == []
    assert manifest["skipped_ids"] == ["scripted-full::e2e/b.spec.ts::skipped"]
