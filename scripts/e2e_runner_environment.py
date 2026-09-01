"""Run-scoped child environment for the canonical E2E lifecycle."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from e2e_runner_contract import E2eContractError, E2eDsns, Lane, Project

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_VERSION_PATTERN = re.compile(r"v(\d+)\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")


def assert_node22() -> None:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME"}}
    node = shutil.which("node", path=env.get("PATH"))
    if node is None:
        raise E2eContractError("node_version_unavailable")
    try:
        result = subprocess.run(  # noqa: S603 - resolved Node executable with sanitized PATH
            [node, "--version"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise E2eContractError("node_version_unavailable") from error
    match = NODE_VERSION_PATTERN.fullmatch(result.stdout.strip())
    if result.returncode != 0 or match is None or match.group(1) != "22":
        raise E2eContractError("node_major_mismatch")


def _optional_frontend_controls(project: Project) -> dict[str, str]:
    controls: dict[str, str] = {}
    runtime = os.environ.get("NEXT_PUBLIC_CHAT_RUNTIME")
    if runtime is not None:
        match runtime:
            case "legacy" | "langgraph_v3":
                controls["NEXT_PUBLIC_CHAT_RUNTIME"] = runtime
            case _:
                raise E2eContractError("invalid_chat_runtime")
    capture_tour = os.environ.get("E2E_CAPTURE_TOUR")
    if capture_tour is not None:
        if project != "scripted-capture" or capture_tour != "1":
            raise E2eContractError("invalid_capture_tour")
        controls["E2E_CAPTURE_TOUR"] = capture_tour
    return controls


def build_lane_environment(
    lane: Lane, project: Project, dsns: E2eDsns, run_root: Path
) -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
        "USER",
        "LOGNAME",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    frontend_port, backend_port = (3100, 8101) if lane == "scripted" else (3200, 8201)
    env.update(
        {
            "MOLDY_DISABLE_ENV_FILE": "true",
            "MOLDY_GATE_PYTHON": sys.executable,
            "PYTHON_DOTENV_DISABLED": "1",
            "MOLDY_TEST_RUN_ROOT": str(run_root),
            "MOLDY_BACKEND_SOURCE_ROOT": str(REPO_ROOT / "backend"),
            "DATABASE_URL": dsns.async_url,
            "DATABASE_URL_SYNC": dsns.sync_url,
            "INTEGRATION_DATABASE_URL": dsns.integration_url,
            "E2E_LANE": lane,
            "E2E_PROJECT": project,
            "E2E_WORKERS": "1",
            "E2E_RETRIES": "0",
            "PW_REUSE_EXISTING_SERVER": "0",
            "E2E_FRONTEND_PORT": str(frontend_port),
            "E2E_BACKEND_PORT": str(backend_port),
            "E2E_SEED_USER_ENABLED": "true",
            "E2E_USER_EMAIL": f"e2e-{secrets.token_hex(8)}@moldy.dev",
            "E2E_USER_PASSWORD": secrets.token_urlsafe(24),
            "E2E_USER_NAME": "Isolated E2E User",
            "ENCRYPTION_KEYS": secrets.token_hex(32),
            "JWT_SECRET": secrets.token_urlsafe(48),
            "RATE_LIMIT_ENABLED": "false",
            "E2E_TEST_HELPERS_ENABLED": "true",
            "CHECKPOINTER_POOL_MIN_SIZE": "1",
            "CHECKPOINTER_POOL_MAX_SIZE": "2",
            "E2E_AUTH_STATE_PATH": str(run_root / "frontend/auth" / project / f"{lane}-user.json"),
            "E2E_NEXT_BUILD_DIR": str(run_root / "frontend/.next" / project),
            "E2E_RESULTS_DIR": str(run_root / "frontend/test-results" / project),
            "PLAYWRIGHT_JUNIT_OUTPUT_NAME": str(
                run_root / "frontend/test-results" / project / "junit.xml"
            ),
            "E2E_SCRIPTED_MODEL_ENABLED": "true" if lane == "scripted" else "false",
        }
    )
    env.update(_optional_frontend_controls(project))
    return env
