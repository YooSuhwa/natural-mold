"""Baseline: run probe.py through today's host executor contract.

Run inside the backend image with the current ``backend/app`` mounted, fake
secrets in the environment and a throwaway ``/app/data`` (see README.md). It
uses the real allowlist (``_prepare_skill_subprocess_args``) and builds the
child environment the way ``skill_executor.py`` does, then spawns the probe
with ``asyncio.create_subprocess_exec`` like the executor.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from app.agent_runtime.skill_execution_policy import _prepare_skill_subprocess_args

DATA = Path("/app/data")
SKILL = DATA / "tenants/tenant-a/runtime/thread-1/skills/probe-skill"
OUTPUT = DATA / "tenants/tenant-a/conversations/thread-1/outputs"
OTHER_TENANT_FILE = DATA / "tenants/tenant-b/conversations/c1/secret.txt"


async def main() -> None:
    (SKILL / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(Path(__file__).with_name("probe.py"), SKILL / "scripts/probe.py")
    OTHER_TENANT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OTHER_TENANT_FILE.write_text("tenant-b private conversation file")

    resolved = SKILL.resolve()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
        "PYTHONPATH": str(resolved),
        "HOME": str(resolved),
        "SKILL_OUTPUT_DIR": str(OUTPUT),
        "OUTPUTS_DIR": str(OUTPUT),
    }
    command = f"python scripts/probe.py --other-tenant-file {OTHER_TENANT_FILE}"
    args, error = _prepare_skill_subprocess_args(command, resolved=resolved, env=env)
    print(f"allowlist verdict for {command!r}: {'ALLOWED' if error is None else error}")
    assert args is not None

    OUTPUT.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(resolved),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    print(stdout.decode())
    if stderr:
        print("STDERR:", stderr.decode()[-2000:])


if __name__ == "__main__":
    asyncio.run(main())
