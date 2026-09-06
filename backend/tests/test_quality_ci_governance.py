from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET_EXPRESSION = re.compile(r"\$\{\{\s*secrets\.[^}]+\}\}")


def _workflow(name: str) -> str:
    return (REPO_ROOT / ".github/workflows" / name).read_text(encoding="utf-8")


def _workflow_steps(workflow: str) -> tuple[str, ...]:
    """Parse the six-space-indented GitHub Actions step blocks."""
    lines = workflow.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("      - ")]
    return tuple(
        "\n".join(lines[start:end])
        for start, end in zip(starts, [*starts[1:], len(lines)], strict=True)
    )


def _named_step(steps: tuple[str, ...], name: str) -> str:
    """Return one named step or fail the structural contract."""
    matching = tuple(step for step in steps if f"- name: {name}" in step.splitlines()[0])
    assert len(matching) == 1
    return matching[0]


def _run_body(step: str) -> str:
    """Extract a literal or block run body from one parsed workflow step."""
    lines = step.splitlines()
    run_index = next(index for index, line in enumerate(lines) if line.startswith("        run:"))
    body = []
    for line in lines[run_index + 1 :]:
        if not line.startswith("          "):
            break
        body.append(line[10:])
    if body:
        return "\n".join(body).strip()
    return lines[run_index].removeprefix("        run: ").strip()


def test_ci_lanes_are_exact_and_mutually_exclusive() -> None:
    pull_request = _workflow("ci.yml")
    nightly = _workflow("nightly-e2e.yml")
    live = _workflow("live-e2e.yml")

    assert "if: github.event_name == 'pull_request'" in pull_request
    assert "--project=scripted-smoke --workers=1" in pull_request
    assert "schedule:" not in pull_request
    assert "--project=scripted-full --workers=1" in nightly
    assert "schedule:" in nightly and "workflow_dispatch:" in nightly
    assert "pull_request:" not in nightly
    assert "--project=live-manual --workers=1" in live
    assert "schedule:" not in live and "pull_request:" not in live
    trigger_start = live.index("on:\n")
    trigger_end = live.index("concurrency:", trigger_start)
    assert live[trigger_start:trigger_end] == "on:\n  workflow_dispatch:\n\n"

    steps = _workflow_steps(live)
    execution = _named_step(steps, "Run the exact four-case live allowlist")
    secret_steps = tuple(step for step in steps if SECRET_EXPRESSION.search(step))
    assert secret_steps == (execution,)
    assert {
        match.group(1) for match in re.finditer(r"^\s+(E2E_LLM_[A-Z_]+):", execution, re.MULTILINE)
    } == {"E2E_LLM_BASE_URL", "E2E_LLM_API_KEY", "E2E_LLM_MODEL"}
    assert set(SECRET_EXPRESSION.findall(execution)) == {
        "${{ secrets.E2E_LLM_BASE_URL }}",
        "${{ secrets.E2E_LLM_API_KEY }}",
        "${{ secrets.E2E_LLM_MODEL }}",
    }
    assert _run_body(execution).splitlines() == [
        'E2E_RUN_MANIFEST="$PWD/.omo/evidence/project-restart-consolidated-roadmap/'
        'ci-live-manual.json" ' + "\\",
        "  pnpm --dir frontend test:e2e:live -- --project=live-manual --workers=1",
    ]
    assert all(not SECRET_EXPRESSION.search(step) for step in steps if step != execution)
    assert _run_body(_named_step(steps, "Govern skips")) == "pnpm --dir frontend lint:e2e-skips"


def test_all_browser_e2e_workflows_install_locked_chromium_before_execution() -> None:
    execution_steps = {
        "ci.yml": "Govern skips and run deterministic PR smoke",
        "nightly-e2e.yml": "Govern skips and run deterministic full suite",
        "live-e2e.yml": "Run the exact four-case live allowlist",
    }
    for workflow_name, execution_name in execution_steps.items():
        steps = _workflow_steps(_workflow(workflow_name))
        install = _named_step(steps, "Install Playwright Chromium")
        execution = _named_step(steps, execution_name)

        assert _run_body(install) == (
            "pnpm --dir frontend exec playwright install --with-deps chromium"
        )
        assert steps.index(install) < steps.index(execution)


def test_artifacts_upload_only_after_redaction_cleanup_with_fixed_retention() -> None:
    expected = {"ci.yml": "14", "nightly-e2e.yml": "30", "live-e2e.yml": "30"}
    for name, days in expected.items():
        workflow = _workflow(name)
        validation = workflow.index("Validate cleanup and redacted artifact contract")
        upload = workflow.index("actions/upload-artifact@v4", validation)
        assert validation < upload
        assert f"retention-days: {days}" in workflow
        assert "if-no-files-found: error" in workflow


def test_pr_smoke_preserves_failure_artifacts_for_diagnosis() -> None:
    workflow_steps = _workflow_steps(_workflow("ci.yml"))
    validation = _named_step(workflow_steps, "Validate cleanup and redacted artifact contract")
    uploads = tuple(step for step in workflow_steps if "actions/upload-artifact@v4" in step)
    diagnostic_upload, full_upload = uploads[:2]

    assert "id: pr_smoke_artifact_contract" in validation
    assert "if: ${{ always() }}" in validation
    assert "--print-artifact-metadata" in validation
    assert "outputs.artifact_scope == 'manifest-only'" in diagnostic_upload
    assert "outputs.artifact_scope == 'full'" in full_upload
    assert "output/e2e-captures/" not in diagnostic_upload
    assert "outputs.artifact_directory" in full_upload
    assert "output/e2e-captures/" not in full_upload
    assert "ci-pr-smoke.json" in diagnostic_upload
    assert "ci-pr-smoke.json" in full_upload


def test_changed_python_type_ratchet_bootstrap_exception_is_bounded() -> None:
    type_ratchet = _named_step(_workflow_steps(_workflow("ci.yml")), "Changed Python type ratchet")
    expected_exception = (
        "continue-on-error: ${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.number == 300 }}"
    )

    assert _run_body(type_ratchet) == (
        'uv run python scripts/check_changed_python_types.py --base "$QUALITY_BASE_SHA"'
    )
    assert expected_exception in type_ratchet
    assert "continue-on-error: true" not in type_ratchet
    assert "|| true" not in _run_body(type_ratchet)


def test_all_deterministic_profiles_forbid_retries() -> None:
    config = (REPO_ROOT / "scripts/project-gates.json").read_text(encoding="utf-8")
    playwright = (REPO_ROOT / "frontend/playwright.config.ts").read_text(encoding="utf-8")

    assert '"retries": 0' in config
    assert "retries: executionPolicy.retries" in playwright
    for workflow_name in ("ci.yml", "nightly-e2e.yml", "live-e2e.yml"):
        assert "--workers=1" in _workflow(workflow_name)


def test_ci_runs_both_provenance_bound_coverage_gates_with_full_history() -> None:
    workflow = _workflow("ci.yml")

    assert "uv run python ../scripts/run_coverage_gate.py backend" in workflow
    assert "python3 ../scripts/run_coverage_gate.py frontend" in workflow
    frontend = workflow[workflow.index("  frontend:") : workflow.index("  pr-scripted-smoke:")]
    assert "fetch-depth: 0" in frontend


def test_frontend_ci_provisions_backend_python_before_script_tests() -> None:
    workflow = _workflow("ci.yml")
    frontend = workflow[workflow.index("  frontend:") : workflow.index("  pr-scripted-smoke:")]
    backend_sync = "\n".join(
        (
            "      - run: uv sync --frozen --all-extras",
            "        working-directory: backend",
        )
    )

    assert "- uses: astral-sh/setup-uv@v5" in frontend
    assert "cache-dependency-glob: backend/uv.lock" in frontend
    assert backend_sync in frontend
    assert frontend.index(backend_sync) < frontend.index("- run: pnpm exec vitest run")


def test_backend_ci_provisions_frontend_dependencies_before_isolated_pytest() -> None:
    workflow = _workflow("ci.yml")
    backend = workflow[workflow.index("  backend:") : workflow.index("  backend-typecheck:")]
    workspace_install = "\n".join(
        (
            "      - run: pnpm install --frozen-lockfile",
            "        working-directory: .",
        )
    )
    isolated_pytest = (
        "bash scripts/run-isolated-command.sh --cwd backend -- "
        "uv run pytest -q -n 4 --ignore=tests/integration"
    )

    assert "- uses: pnpm/action-setup@v4" in backend
    assert "- uses: actions/setup-node@v4" in backend
    assert "node-version-file: .node-version" in backend
    assert workspace_install in backend
    assert backend.index(workspace_install) < backend.index(isolated_pytest)


def test_pr_smoke_uploads_only_the_validated_export_directory() -> None:
    workflow_steps = _workflow_steps(_workflow("ci.yml"))
    validation = _named_step(workflow_steps, "Validate cleanup and redacted artifact contract")
    uploads = tuple(step for step in workflow_steps if "actions/upload-artifact@v4" in step)
    full_upload = uploads[1]

    assert "--print-artifact-metadata" in validation
    assert '>> "$GITHUB_OUTPUT"' in validation
    assert "steps.pr_smoke_artifact_contract.outputs.artifact_directory" in full_upload
    assert "output/e2e-captures/" not in full_upload
