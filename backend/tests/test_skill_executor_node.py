from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from app.agent_runtime.skill_executor import (
    _create_skill_execute_tool,
    _prepare_skill_subprocess_args,
)
from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext


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


@pytest.mark.asyncio
async def test_execute_in_skill_kills_subprocess_on_cancellation(tmp_path: Path) -> None:
    """run cancel(Stop)이 실행 중인 skill subprocess를 고아로 남기지 않는다 (P3.2).

    wait_for 가 취소되면 timeout kill 경로도 함께 취소되므로, CancelledError
    분기가 직접 proc.kill() 하지 않으면 스크립트가 취소 후에도 계속 돈다.
    """
    runtime_root = tmp_path / "runtime"
    skill_dir = runtime_root / "sleeper"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    pid_file = tmp_path / "sleeper.pid"
    (scripts_dir / "sleep_forever.py").write_text(
        "import os, pathlib, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    descriptor = SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug="sleeper",
        name="Sleeper",
        description="sleeps forever",
        original_storage_path=skill_dir,
        runtime_storage_path=skill_dir,
    )
    ctx = SkillToolContext(
        thread_id="thread-cancel",
        output_dir=tmp_path / "outputs",
        runtime_root=runtime_root,
        descriptors={"sleeper": descriptor},
    )
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None

    task = asyncio.create_task(
        tool.coroutine(
            skill_directory="/runtime/thread-cancel/skills/sleeper/",
            command="python scripts/sleep_forever.py",
        )
    )
    # subprocess 가 실제로 시작될 때까지 대기 (스크립트가 PID 파일을 씀)
    for _ in range(250):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.02)
    else:
        task.cancel()
        raise AssertionError("skill subprocess never started")
    pid = int(pid_file.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # proc.kill() + wait() 가 실행됐다면 PID 는 곧 사라진다
    for _ in range(150):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.02)
    else:
        os.kill(pid, 9)  # 테스트 실패 시 고아 프로세스 정리
        raise AssertionError("skill subprocess survived cancellation")
