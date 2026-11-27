"""OpenSandbox operations for an authorized, credential-free goal workspace."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from opensandbox import Sandbox, SandboxManager
from opensandbox.models.filesystem import WriteEntry

from app.agent_runtime import skill_sandbox
from app.agent_runtime.skill_sandbox import (
    MAX_OUTPUT_FILE_BYTES,
    MAX_OUTPUT_TOTAL_BYTES,
    OUTPUT_DIR,
    SKILL_DIR,
    SkillSandboxError,
)
from app.config import settings

if TYPE_CHECKING:
    from app.services.goal_sandbox_leases import SandboxClaim

WORK_DIR = "/workspace/intermediate"
# A root-owned directory in the sandbox. Commands run as uid 1000 and cannot
# modify either the snapshot or its manifest while the app retrieves outputs.
_EXPORT = "/workspace/.moldy-export"


def goal_workspace_metadata(workspace_id: UUID) -> dict[str, str]:
    return {"app": "moldy", "kind": "goal_workspace", "workspace_id": str(workspace_id)}


async def open_workspace(claim: SandboxClaim, timeout_seconds: float) -> Sandbox:
    connection = skill_sandbox.connection_config(timeout_seconds)
    lifetime = timedelta(
        seconds=max(settings.goal_sandbox_retention_seconds + 120, timeout_seconds + 300)
    )
    if claim.action == "create":
        return await Sandbox.create(
            settings.skill_sandbox_image,
            connection_config=connection,
            network_policy=skill_sandbox.network_policy(()),
            resource={"cpu": settings.skill_sandbox_cpu, "memory": settings.skill_sandbox_memory},
            metadata=goal_workspace_metadata(claim.workspace_id),
            timeout=lifetime,
            ready_timeout=timedelta(seconds=60),
        )
    if claim.action not in {"resume", "connect"} or claim.provider_id is None:
        raise SkillSandboxError("goal workspace is unavailable.")
    # Verify the resource before a resume, which is itself a remote side effect.
    async with await SandboxManager.create(connection_config=connection) as manager:
        info = await manager.get_sandbox_info(claim.provider_id)
        expected = goal_workspace_metadata(claim.workspace_id)
        if not all((info.metadata or {}).get(key) == value for key, value in expected.items()):
            raise SkillSandboxError("goal workspace identity changed.")
    open_call = Sandbox.resume if claim.action == "resume" else Sandbox.connect
    sandbox = await open_call(
        claim.provider_id, connection_config=skill_sandbox.connection_config(timeout_seconds)
    )
    try:
        await sandbox.renew(lifetime)
    except BaseException:
        await sandbox.close()
        raise
    return sandbox


async def _checked(sandbox: Sandbox, command: str) -> str:
    result = await sandbox.commands.run(command)
    if result.exit_code != 0:
        raise SkillSandboxError("goal workspace files could not be verified.")
    return "".join(f"{message.text}\n" for message in result.logs.stdout)


_PACKAGE_ROOT = "/workspace/.moldy-skills"


async def _require_idle_processes(sandbox: Sandbox) -> None:
    # A first stage must not keep a process that can modify the next stage's
    # private files after the tool invocation has ended.
    script = """
import pathlib
for status in pathlib.Path('/proc').glob('[0-9]*/status'):
    try:
        lines = status.read_text().splitlines()
    except FileNotFoundError:
        continue
    if any(line.startswith('Uid:') and '1000' in line.split()[1:] for line in lines):
        raise SystemExit(1)
"""
    await _checked(sandbox, shlex.join(["python", "-I", "-c", script]))


async def prepare_workspace(
    sandbox: Sandbox, files: list[tuple[str, bytes]], *, new: bool, skill_hash: str
) -> None:
    """Select one measured skill's immutable code, preserving only the goal's intermediate files."""
    await _require_idle_processes(sandbox)
    if not re.fullmatch(r"[0-9a-f]{64}", skill_hash):
        raise SkillSandboxError("goal workspace skill digest is invalid.")
    if new:
        # /workspace is root-owned. A skill cannot replace its code, the export
        # snapshot or the shared intermediate directory with a symlink.
        await _checked(
            sandbox,
            f"install -d -m 0755 /workspace {_PACKAGE_ROOT} && "
            f"install -d -m 0700 -o 1000 -g 1000 {WORK_DIR} {OUTPUT_DIR}",
        )
    package = f"{_PACKAGE_ROOT}/{skill_hash}"
    ready = f"{_PACKAGE_ROOT}/{skill_hash}.ready"
    status = await sandbox.commands.run(f"test -f {ready}")
    if status.exit_code == 1:
        await _checked(sandbox, f"install -d -m 0755 {package}")
        await sandbox.files.write_files(
            [WriteEntry(path=f"{package}/{name}", data=content) for name, content in files]
        )
        await _checked(
            sandbox, f"chown -R 0:0 {package} && chmod -R a+rX,a-w {package} && touch {ready}"
        )
    elif status.exit_code != 0:
        raise SkillSandboxError("goal workspace skill package is unavailable.")
    await _checked(
        sandbox, f"ln -sfnT {package} {SKILL_DIR} && find {OUTPUT_DIR} -mindepth 1 -delete"
    )


# Snapshot only top-level, single-linked regular files owned by the skill uid.
# Open files relative to the fixed directory without following links; recheck
# the opened descriptor and bound actual reads even if the file changes.
_SNAPSHOT = """
import hashlib, json, os, shutil, stat, sys
source, target, per_file, total_limit = sys.argv[1:]
shutil.rmtree(target, ignore_errors=True)
os.mkdir(target, 0o700)
result = {}
total = 0
d = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for entry in os.scandir(d):
        if not entry.is_file(follow_symlinks=False):
            continue
        fd = os.open(entry.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=d)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 1000:
                raise ValueError('unowned output')
            if info.st_size > int(per_file) or info.st_size + total > int(total_limit):
                raise ValueError('output size')
            digest = hashlib.sha256()
            size = 0
            with open(os.path.join(target, entry.name), 'xb') as out:
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > info.st_size:
                        raise ValueError('changed output')
                    out.write(chunk)
                    digest.update(chunk)
            if size != info.st_size:
                raise ValueError('changed output')
            total += size
            result[entry.name] = [size, digest.hexdigest()]
            if len(result) > 1024:
                raise ValueError('too many outputs')
finally:
    os.close(d)
print(json.dumps(result, ensure_ascii=True))
"""


async def collect_outputs(sandbox: Sandbox) -> dict[str, bytes]:
    await _require_idle_processes(sandbox)
    command = shlex.join(
        [
            "python",
            "-I",
            "-c",
            _SNAPSHOT,
            OUTPUT_DIR,
            _EXPORT,
            str(MAX_OUTPUT_FILE_BYTES),
            str(MAX_OUTPUT_TOTAL_BYTES),
        ]
    )
    result = await _checked(sandbox, command)
    try:
        entries = json.loads(result)
    except (ValueError, TypeError) as exc:
        raise SkillSandboxError("goal workspace output manifest is invalid.") from exc
    if not isinstance(entries, dict) or len(entries) > 1024:
        raise SkillSandboxError("goal workspace output manifest is invalid.")
    files: dict[str, bytes] = {}
    size_total = 0
    for name, expected in entries.items():
        if (
            not valid_output_name(name)
            or not isinstance(expected, list)
            or len(expected) != 2
            or not isinstance(expected[0], int)
            or not 0 <= expected[0] <= MAX_OUTPUT_FILE_BYTES
        ):
            raise SkillSandboxError("goal workspace output manifest is invalid.")
        size_total += expected[0]
        if size_total > MAX_OUTPUT_TOTAL_BYTES:
            raise SkillSandboxError("goal workspace output limit exceeded.")
        content = await sandbox.files.read_bytes(f"{_EXPORT}/{name}")
        if len(content) != expected[0] or hashlib.sha256(content).hexdigest() != expected[1]:
            raise SkillSandboxError("goal workspace output changed during collection.")
        files[name] = content
    return files


def valid_output_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and name not in {"", ".", ".."}
        and not name.startswith(".")
        and not any(ord(c) < 32 or c in "/\\" for c in name)
    )


def output_manifest(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}


def publish_outputs(directory: Path, files: dict[str, bytes]) -> None:
    """Publish to the server-resolved directory. A retried receipt never overwrites a newer file."""
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise SkillSandboxError("goal output directory is invalid.")
    for name, content in files.items():
        if not valid_output_name(name):
            raise SkillSandboxError("goal workspace output filename is invalid.")
        target = directory / name
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".goal-output-") as staging:
            staging.write(content)
            staging.flush()
            os.fsync(staging.fileno())
            try:
                os.link(staging.name, target)
            except FileExistsError:
                try:
                    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                except OSError as exc:
                    raise SkillSandboxError(
                        "goal workspace output path conflicts with an existing file."
                    ) from exc
                with os.fdopen(descriptor, "rb") as existing:
                    info = os.fstat(existing.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_size != len(content)
                        or hashlib.file_digest(existing, "sha256").digest()
                        != hashlib.sha256(content).digest()
                    ):
                        raise SkillSandboxError(
                            "goal workspace output filename is already in use."
                        ) from None
