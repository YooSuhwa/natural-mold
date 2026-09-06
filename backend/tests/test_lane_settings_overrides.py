"""Case-insensitive lane path precedence at the real settings import boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LANE_FIELDS = (
    "data_root",
    "skill_storage_dir",
    "k_skill_sync_dir",
    "k_skill_builtin_storage_dir",
    "conversation_output_dir",
    "upload_dir",
    "artifact_storage_dir",
    "agent_image_dir",
    "user_avatar_dir",
)


def _probe(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    names = ",".join(repr(name) for name in LANE_FIELDS)
    code = (
        "import json; from app.config import settings; "
        f"print(json.dumps({{name:getattr(settings,name) for name in ({names},)}}))"
    )
    excluded = {name.upper() for name in LANE_FIELDS} | {name.lower() for name in LANE_FIELDS}
    inherited = {
        name: value
        for name, value in os.environ.items()
        if name not in excluded and name.lower() not in LANE_FIELDS
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        env={
            **inherited,
            "PYTHONPATH": str(BACKEND_ROOT),
            "MOLDY_DISABLE_ENV_FILE": "true",
            **environment,
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("field_name", LANE_FIELDS)
@pytest.mark.parametrize("case", ["upper", "lower", "mixed"])
def test_lane_path_preserves_case_insensitive_explicit_override(
    tmp_path: Path, field_name: str, case: str
) -> None:
    # Given: one contained explicit override using a Pydantic-supported env-name case.
    run_root = tmp_path / "run"
    explicit = run_root / "custom" / field_name
    environment_name = field_name.upper()
    if case == "lower":
        environment_name = environment_name.lower()
    elif case == "mixed":
        environment_name = "".join(
            character.lower() if index % 2 else character
            for index, character in enumerate(environment_name)
        )

    # When: the real module-level settings boundary loads the lane.
    result = _probe({"MOLDY_TEST_RUN_ROOT": str(run_root), environment_name: str(explicit)})

    # Then: the parsed explicit value survives lane-default derivation.
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[field_name] == str(explicit)


def test_lane_path_defaults_remain_contained_and_outside_override_fails(tmp_path: Path) -> None:
    # Given: an absent override and one explicit value outside the lane root.
    run_root = tmp_path / "run"

    # When: defaults and an escaping lowercase override are loaded independently.
    defaults = _probe({"MOLDY_TEST_RUN_ROOT": str(run_root)})
    escaped = _probe(
        {
            "MOLDY_TEST_RUN_ROOT": str(run_root),
            "upload_dir": str(tmp_path / "outside"),
        }
    )

    # Then: defaults are contained and the explicit escape fails closed.
    assert defaults.returncode == 0, defaults.stderr
    assert all(
        Path(value).is_relative_to(run_root) for value in json.loads(defaults.stdout).values()
    )
    assert escaped.returncode != 0
    assert str(tmp_path / "outside") not in escaped.stderr
