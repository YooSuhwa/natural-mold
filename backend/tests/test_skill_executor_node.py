from __future__ import annotations

from pathlib import Path

from app.agent_runtime.skill_executor import _prepare_skill_subprocess_args


def _make_skill(tmp_path: Path) -> tuple[Path, Path]:
    skill_dir = tmp_path / "skill"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    script = scripts_dir / "create_docx.cjs"
    script.write_text("console.log('ok')\n")
    return skill_dir, script


def test_prepare_skill_subprocess_args_allows_node_script_inside_skill(
    tmp_path: Path,
) -> None:
    skill_dir, _script = _make_skill(tmp_path)
    output_dir = tmp_path / "outputs"

    args, error = _prepare_skill_subprocess_args(
        "node scripts/create_docx.cjs --input $OUTPUTS_DIR/docx-spec.json",
        resolved=skill_dir,
        env={"OUTPUTS_DIR": str(output_dir)},
    )

    assert error is None
    assert args is not None
    assert Path(args[0]).name == "node"
    assert args[1] == "scripts/create_docx.cjs"
    assert str(output_dir / "docx-spec.json") in args


def test_prepare_skill_subprocess_args_rejects_node_eval(tmp_path: Path) -> None:
    skill_dir, _script = _make_skill(tmp_path)

    for command in (
        'node -e "console.log(1)"',
        'node --eval "console.log(1)"',
    ):
        args, error = _prepare_skill_subprocess_args(command, resolved=skill_dir, env={})

        assert args is None
        assert error == "Error: node command must be `node scripts/<file>.cjs ...`."


def test_prepare_skill_subprocess_args_rejects_node_path_escape(tmp_path: Path) -> None:
    skill_dir, _script = _make_skill(tmp_path)
    outside = tmp_path / "escape.cjs"
    outside.write_text("console.log('escape')\n")

    args, error = _prepare_skill_subprocess_args(
        "node ../escape.cjs",
        resolved=skill_dir,
        env={},
    )

    assert args is None
    assert error == "Error: node script must be within the skill directory."


def test_prepare_skill_subprocess_args_rejects_unsupported_node_script_extension(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skill"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "create_docx.txt").write_text("console.log('bad')\n")

    args, error = _prepare_skill_subprocess_args(
        "node scripts/create_docx.txt",
        resolved=skill_dir,
        env={},
    )

    assert args is None
    assert error == "Error: node script must use .js, .cjs, or .mjs."


def test_prepare_skill_subprocess_args_rejects_npm_and_npx(tmp_path: Path) -> None:
    skill_dir, _script = _make_skill(tmp_path)

    for command in ("npm run build", "npx anything"):
        args, error = _prepare_skill_subprocess_args(command, resolved=skill_dir, env={})

        assert args is None
        assert error == "Error: only python, node, or curl commands are allowed."
