"""Hermetic settings and runtime path contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.config import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LANE_ENVIRONMENT_NAMES = {
    "DATA_ROOT",
    "SKILL_STORAGE_DIR",
    "K_SKILL_SYNC_DIR",
    "K_SKILL_BUILTIN_STORAGE_DIR",
    "CONVERSATION_OUTPUT_DIR",
    "UPLOAD_DIR",
    "ARTIFACT_STORAGE_DIR",
    "AGENT_IMAGE_DIR",
    "USER_AVATAR_DIR",
}


def _config_probe(cwd: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    probe = (
        "import json; from app.config import settings; "
        "from app.agent_runtime.runtime_config import _DATA_DIR; "
        "from app.marketplace.skill_runtime import _per_thread_runtime_root; "
        "print(json.dumps({'sentinel': settings.openai_api_key, 'data_root': settings.data_root, "
        "'skills': settings.skill_storage_dir, 'conversations': settings.conversation_output_dir, "
        "'uploads': settings.upload_dir, 'artifacts': settings.artifact_storage_dir, "
        "'agents': settings.agent_image_dir, 'users': settings.user_avatar_dir, "
        "'runtime': str(_DATA_DIR), "
        "'thread_skills': str(_per_thread_runtime_root(_DATA_DIR, 'thread-fixture'))}))"
    )
    inherited = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "OPENAI_API_KEY",
            "MOLDY_DISABLE_ENV_FILE",
            "MOLDY_TEST_RUN_ROOT",
            "MOLDY_TEST_PROBE_CONTROL",
            "MOLDY_TEST_PROBE_SENTINEL",
            "PYTHON_DOTENV_DISABLED",
            *LANE_ENVIRONMENT_NAMES,
        }
    }
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=cwd,
        env={**inherited, "PYTHONPATH": str(BACKEND_ROOT), **environment},
        capture_output=True,
        text=True,
        check=False,
    )


def _main_entrypoint_probe(
    cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    probe = (
        "import json,os; import app.main; "
        "print(json.dumps({'sentinel': 'MOLDY_TEST_PROBE_SENTINEL' in os.environ, "
        "'control': os.environ.get('MOLDY_TEST_PROBE_CONTROL')}))"
    )
    inherited = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "MOLDY_DISABLE_ENV_FILE",
            "MOLDY_TEST_PROBE_CONTROL",
            "MOLDY_TEST_PROBE_SENTINEL",
            "PYTHON_DOTENV_DISABLED",
        }
    }
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=cwd,
        env={**inherited, "PYTHONPATH": str(BACKEND_ROOT), **environment},
        capture_output=True,
        text=True,
        check=False,
    )


def test_default_path_values_are_byte_equivalent() -> None:
    # Given: settings constructed without the test runner contract.

    # When: defaults are loaded without an env file.
    configured = Settings(_env_file=None)

    # Then: all pre-existing development path bytes are unchanged.
    assert {
        "data_root": configured.data_root,
        "skills": configured.skill_storage_dir,
        "conversations": configured.conversation_output_dir,
        "uploads": configured.upload_dir,
        "artifacts": configured.artifact_storage_dir,
        "agents": configured.agent_image_dir,
        "users": configured.user_avatar_dir,
    } == {
        "data_root": "./data",
        "skills": "./data/skills",
        "conversations": "./data/conversations",
        "uploads": "./data/uploads",
        "artifacts": "./data/artifacts",
        "agents": "./data/agents",
        "users": "./data/users",
    }


def test_env_file_opt_out_and_run_root_containment(tmp_path: Path) -> None:
    # Given: a cwd-only sentinel .env and a runner-owned unique root.
    (tmp_path / ".env").write_text("OPENAI_API_KEY=env-file-only-sentinel\n")
    run_root = tmp_path / "owned-run"

    # When: settings are imported under the explicit runner contract.
    result = _config_probe(
        tmp_path,
        {"MOLDY_DISABLE_ENV_FILE": "true", "MOLDY_TEST_RUN_ROOT": str(run_root)},
    )

    # Then: the sentinel is absent and every lane-owned path is contained.
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["sentinel"] == ""
    for key in (
        "data_root",
        "skills",
        "conversations",
        "uploads",
        "artifacts",
        "agents",
        "users",
        "runtime",
    ):
        assert Path(payload[key]).is_relative_to(run_root)


def test_process_env_precedence_is_preserved_inside_run_root(tmp_path: Path) -> None:
    # Given: a process-env override that remains inside the unique root.
    run_root = tmp_path / "owned-run"
    explicit_uploads = run_root / "custom" / "uploads"

    # When: settings are imported.
    result = _config_probe(
        tmp_path,
        {
            "MOLDY_DISABLE_ENV_FILE": "true",
            "MOLDY_TEST_RUN_ROOT": str(run_root),
            "UPLOAD_DIR": str(explicit_uploads),
        },
    )

    # Then: the explicit safe override wins over the derived default.
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["uploads"] == str(explicit_uploads)


def test_run_root_rejects_path_escape(tmp_path: Path) -> None:
    # Given: an explicit lane path outside the runner-owned root.
    run_root = tmp_path / "owned-run"

    # When: settings are imported with the escaping override.
    result = _config_probe(
        tmp_path,
        {
            "MOLDY_DISABLE_ENV_FILE": "true",
            "MOLDY_TEST_RUN_ROOT": str(run_root),
            "UPLOAD_DIR": str(tmp_path / "outside"),
        },
    )

    # Then: import fails closed without disclosing the supplied path.
    assert result.returncode != 0
    assert str(tmp_path / "outside") not in result.stderr


def test_disable_env_file_requires_exact_true(tmp_path: Path) -> None:
    # Given: a cwd .env and a near-miss opt-out value.
    (tmp_path / ".env").write_text("OPENAI_API_KEY=env-file-only-sentinel\n")

    # When: settings are imported without the exact runner-only value.
    result = _config_probe(tmp_path, {"MOLDY_DISABLE_ENV_FILE": "1"})

    # Then: normal development env-file loading remains intact.
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["sentinel"] == "env-file-only-sentinel"


def test_app_main_respects_explicit_env_file_opt_out(tmp_path: Path) -> None:
    # Given: the real app entrypoint starts from a cwd with a fresh dummy sentinel.
    (tmp_path / ".env").write_text("MOLDY_TEST_PROBE_SENTINEL=dummy-file-only\n")

    # When: the Moldy runner contract disables env-file loading.
    result = _main_entrypoint_probe(
        tmp_path,
        {
            "MOLDY_DISABLE_ENV_FILE": "true",
            "MOLDY_TEST_PROBE_CONTROL": "process-control",
        },
    )

    # Then: the file-only sentinel is absent and process env remains available.
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"sentinel": False, "control": "process-control"}


def test_runtime_data_root_preserves_default_from_alternate_cwd(tmp_path: Path) -> None:
    # Given: settings are imported from a cwd unrelated to the backend.

    # When: the runtime root is resolved without the isolation contract.
    result = _config_probe(tmp_path, {"MOLDY_DISABLE_ENV_FILE": "true"})

    # Then: the canonical data root remains the historical backend/data directory.
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)["runtime"]) == BACKEND_ROOT / "data"


def test_runtime_data_root_honors_absolute_override(tmp_path: Path) -> None:
    # Given: an explicit absolute data-root override from an alternate cwd.
    explicit_data_root = tmp_path / "explicit-data"

    # When: the runtime config is imported.
    result = _config_probe(
        tmp_path,
        {"MOLDY_DISABLE_ENV_FILE": "true", "DATA_ROOT": str(explicit_data_root)},
    )

    # Then: the explicit canonical data root wins without an added runtime suffix.
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)["runtime"]) == explicit_data_root


def test_isolated_runtime_consumer_appends_runtime_once(tmp_path: Path) -> None:
    # Given: one runner-owned root from an alternate cwd.
    run_root = tmp_path / "owned-run"

    # When: the canonical data root and marketplace per-thread consumer resolve.
    result = _config_probe(
        tmp_path,
        {"MOLDY_DISABLE_ENV_FILE": "true", "MOLDY_TEST_RUN_ROOT": str(run_root)},
    )

    # Then: data is contained and the consumer contributes exactly one runtime segment.
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["runtime"]) == run_root / "backend" / "data"
    assert Path(payload["thread_skills"]) == (
        run_root / "backend" / "data" / "runtime" / "thread-fixture" / "skills"
    )
