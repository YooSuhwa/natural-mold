"""Pure scenario helpers shared by the isolated E2E runner."""

from __future__ import annotations

from pathlib import Path

from e2e_runner_contract import Lane, Project
from e2e_runner_manifest import RunFacts
from e2e_runner_playwright import (
    PlaywrightNode,
    canonical_live_cases_json,
    canonical_live_nodes,
    canonical_live_title_filter,
)
from e2e_runner_runtime import E2eResources


def initial_ownership() -> dict[str, bool]:
    return dict.fromkeys(("run_root", "database", "backend", "frontend", "proxy"), False)


def resource_facts(resources: E2eResources | None, run_id: str) -> RunFacts:
    if resources is None:
        return RunFacts(run_id, None, None, None, None, False)
    return RunFacts(
        resources.run_id,
        resources.server_version,
        resources.alembic_head,
        resources.alembic_current,
        resources.schema_fingerprint,
        resources.second_upgrade_idempotent,
    )


def cleanup_passed(cleanup: dict[str, bool | None], *, resources_acquired: bool) -> bool:
    foreign = cleanup.get("foreign_containers_preserved")
    return all(
        value is True for key, value in cleanup.items() if key != "foreign_containers_preserved"
    ) and (foreign is True or (foreign is None and not resources_acquired))


def live_egress_passed(egress: dict[str, object]) -> bool:
    if egress.get("enabled") is not True or egress.get("clean_stop") is not True:
        return False
    records = egress.get("records")
    if not isinstance(records, list):
        return False
    return any(
        isinstance(record, dict)
        and record.get("method") == "POST"
        and record.get("path_class") == "chat_completions"
        and isinstance(record.get("status"), int)
        and 200 <= record["status"] < 300
        and isinstance(record.get("count"), int)
        and record["count"] >= 1
        for record in records
    )


def empty_cleanup() -> dict[str, bool | None]:
    return {
        "cleanup_container_removed": True,
        "owned_label_absent": True,
        "postgres_port_removed": True,
        "owned_database_removed": True,
        "backend_port_removed": True,
        "frontend_port_removed": True,
        "proxy_port_removed": True,
        "process_group_stopped": True,
        "cleanup_run_root_removed": True,
        "foreign_containers_preserved": None,
    }


def playwright_argv(
    project: Project, arguments: tuple[str, ...], *, selection_only: bool
) -> list[str]:
    command = [
        "pnpm",
        "exec",
        "playwright",
        "test",
        f"--project={project}",
        "--workers=1",
        "--retries=0",
    ]
    if selection_only:
        command.append("--reporter=json")
    command.extend(arguments)
    if selection_only:
        command.append("--list")
    return command


def prepare_live_contract(
    lane: Lane, project: Project, env: dict[str, str]
) -> tuple[PlaywrightNode, ...] | None:
    if lane != "live":
        return None
    env.update(
        {
            "E2E_LIVE_CASES_JSON": canonical_live_cases_json(),
            "E2E_LIVE_TITLE_FILTER": canonical_live_title_filter(),
        }
    )
    return canonical_live_nodes(project)


def selection_environment(lane: Lane, env: dict[str, str]) -> dict[str, str]:
    selected = {**env, "E2E_SELECTION_ONLY": "1"}
    if lane == "live":
        selected.update(
            {
                "E2E_LLM_BASE_URL": "http://127.0.0.1:1/v1",
                "E2E_LLM_API_KEY": "selection-only",
                "E2E_LLM_MODEL": "selection-only",
            }
        )
    return selected


def secrets_from_environment(
    env: dict[str, str], *, postgres_password: str = ""
) -> tuple[str, ...]:
    """Collect exact values that the artifact exporter must reject."""
    names = (
        "DATABASE_URL",
        "DATABASE_URL_SYNC",
        "INTEGRATION_DATABASE_URL",
        "E2E_USER_PASSWORD",
        "ENCRYPTION_KEYS",
        "JWT_SECRET",
    )
    configured = tuple(env[name] for name in names)
    return configured + ((postgres_password,) if postgres_password else ())


def ensure_results_directory(run_root: Path, project: Project) -> None:
    results = run_root / "frontend/test-results" / project
    try:
        results.mkdir(mode=0o700)
    except FileExistsError:
        if results.is_symlink() or not results.is_dir():
            raise RuntimeError("unsafe_results_directory") from None
