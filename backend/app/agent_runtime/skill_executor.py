from __future__ import annotations

import asyncio
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings


@dataclass
class SkillScriptResult:
    stdout: str
    stderr: str
    return_code: int
    output_files: list[str] = field(default_factory=list)


async def execute_skill_script(
    skill_dir: str,
    command: str,
    script_timeout: int | None = None,
    output_dir: str | None = None,
) -> SkillScriptResult:
    """Run a python command inside a skill directory with restricted env."""
    timeout = script_timeout or settings.skill_script_timeout
    max_output = settings.skill_max_output_bytes

    parts = shlex.split(command)
    if not parts or parts[0] != "python":
        raise ValueError("Only 'python' commands are allowed")

    # Replace bare 'python' with the current interpreter so that
    # packages installed in the backend venv (e.g. Pillow) are available.
    parts[0] = sys.executable

    skill_path = Path(skill_dir).resolve()

    if output_dir:
        outputs_dir = Path(output_dir).resolve()
        outputs_dir.mkdir(parents=True, exist_ok=True)
    else:
        outputs_dir = skill_path / "_outputs"
        if outputs_dir.is_dir():
            for old in outputs_dir.iterdir():
                if old.is_file():
                    old.unlink()
        outputs_dir.mkdir(parents=True, exist_ok=True)

    env = {
        "PATH": "/usr/bin:/usr/local/bin",
        "PYTHONPATH": str(skill_path),
        "OUTPUTS_DIR": str(outputs_dir),
        "HOME": str(skill_path),
    }

    proc = await asyncio.create_subprocess_exec(
        *parts,
        cwd=str(skill_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return SkillScriptResult(
            stdout="",
            stderr=f"Timeout after {timeout}s",
            return_code=-1,
        )

    stdout = stdout_bytes[:max_output].decode("utf-8", errors="replace")
    stderr = stderr_bytes[:max_output].decode("utf-8", errors="replace")

    # Collect output files
    output_files: list[str] = []
    if outputs_dir.is_dir():
        for f in outputs_dir.iterdir():
            if f.is_file():
                output_files.append(f.name)

    return SkillScriptResult(
        stdout=stdout,
        stderr=stderr,
        return_code=proc.returncode if proc.returncode is not None else 0,
        output_files=output_files,
    )
