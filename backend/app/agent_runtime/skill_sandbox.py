"""Run one ``execute_in_skill`` command in its own OpenSandbox sandbox, then delete it.

docs/design-docs/skill-sandbox-docker.md. The sandbox gets a copy of the skill
directory and of the conversation's current output files, runs the command as an
unprivileged user with the per-command env (credentials never go into the
create-time env, which every later command could read), and the output files the
command wrote are copied back into ``output_dir``. Everything after that (path
rewrite, OUTPUT_FILES, redaction, artifact indexing) is the caller's, unchanged.

Network is denied. A skill that declares ``requires_network`` reaches only the
domains in ``execution_profile.network_domains`` plus the deployment allow-list
(``SKILL_SANDBOX_ALLOWED_DOMAINS``); internal ranges stay denied even then, which
keeps one sandbox away from another sandbox's unauthenticated execd (PoC
2026-11-02, §3.1). Feature plan v2 §15.3.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import settings

if TYPE_CHECKING:
    from opensandbox import Sandbox
    from opensandbox.models.sandboxes import NetworkPolicy

logger = logging.getLogger(__name__)

SKILL_DIR = "/workspace/skill"
OUTPUT_DIR = "/workspace/outputs"
_STARTED = "/workspace/.moldy-started"
SANDBOX_UID = 1000
MAX_OUTPUT_FILE_BYTES = 52_428_800
MAX_OUTPUT_TOTAL_BYTES = 209_715_200
# Seconds the sandbox outlives the command, so a backend that dies mid-run still
# leaves a sandbox the server expires on its own.
_LIFETIME_MARGIN_SECONDS = 120
_INTERNAL_RANGES = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)
_DOMAIN = re.compile(r"^(\*\.)?([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
# Skills that keep a workspace outside the conversation (OpenWiki clones into the
# shared ``workspaces`` directory). A throwaway sandbox cannot hold it yet.
HOST_WORKSPACE_SKILLS = frozenset({"openwiki"})


class SkillSandboxError(RuntimeError):
    """The sandbox could not run the command; the message is shown to the model."""


@dataclass(frozen=True, slots=True)
class SkillRunResult:
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool = False
    sandbox_id: str | None = None


def sandbox_argv(args: list[str]) -> list[str]:
    """``_prepare_skill_subprocess_args`` resolves host binaries; the image has its own."""
    if args[0] == sys.executable:
        return ["python", *args[1:]]
    if Path(args[0]).name != "curl":
        return ["node", *args[1:]]
    return args


def valid_domain(value: object) -> bool:
    """An FQDN or ``*.`` wildcard, lower case (the egress sidecar's target form)."""
    return isinstance(value, str) and _DOMAIN.fullmatch(value) is not None


def network_domains(profile: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Domains the sandbox may reach: none unless the skill declares ``requires_network``."""
    profile = profile or {}
    if profile.get("requires_network") is not True:
        return ()
    declared = profile.get("network_domains")
    allowed = [*(declared if isinstance(declared, list) else [])]
    allowed += settings.skill_sandbox_allowed_domains.split(",")
    return tuple(sorted({d.strip().lower() for d in allowed if valid_domain(d.strip().lower())}))


def network_policy(domains: Sequence[str]) -> NetworkPolicy:
    from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule

    if not domains:
        return NetworkPolicy(defaultAction="deny", egress=[])
    # Rules are evaluated in order: an allowed name resolving to an internal address
    # still hits a deny first.
    rules = [NetworkRule(action="deny", target=cidr) for cidr in _INTERNAL_RANGES]
    rules += [NetworkRule(action="allow", target=domain) for domain in domains]
    return NetworkPolicy(defaultAction="deny", egress=rules)


@contextmanager
def trace_span(run_id: str | None, metadata: dict[str, Any]) -> Iterator[Any]:
    """A ``sandbox`` span on the run's Langfuse trace; nothing when tracing is off."""
    from app.observability import langfuse

    span_cm = None
    if run_id is not None and langfuse.is_langfuse_enabled():
        try:
            client = langfuse._get_langfuse_client()  # noqa: SLF001 - same package's client cache
            span_cm = client.start_as_current_observation(
                as_type="span",
                name="sandbox",
                trace_context={"trace_id": langfuse._trace_id_for_run(client, run_id)},  # noqa: SLF001
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001 - tracing never fails the run
            logger.debug("sandbox trace span not started", exc_info=True)
    if span_cm is None:
        yield None
        return
    with span_cm as span:
        yield span


def _read_tree(root: Path, limit: int) -> list[tuple[str, bytes]]:
    """Regular files under ``root`` (no symlinks), failing before ``limit`` bytes."""
    files: list[tuple[str, bytes]] = []
    total = 0
    for current, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
        for name in names:
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            total += path.stat().st_size
            if total > limit:
                raise SkillSandboxError("skill directory is too large for the sandbox.")
            files.append((path.relative_to(root).as_posix(), path.read_bytes()))
    return files


def _read_outputs(output_dir: Path) -> list[tuple[str, bytes]]:
    """The top-level output files a script may read (the host runner sees them too)."""
    if not output_dir.is_dir():
        return []
    files: list[tuple[str, bytes]] = []
    total = 0
    for path in sorted(output_dir.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        total += path.stat().st_size
        if total > MAX_OUTPUT_TOTAL_BYTES:
            break
        files.append((path.name, path.read_bytes()))
    return files


def _safe_name(name: str) -> bool:
    return bool(name) and name not in {".", ".."} and "/" not in name and "\x00" not in name


def _write_output(output_dir: Path, name: str, content: bytes) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / name
    tmp = output_dir / f".{name}.moldy-tmp"
    tmp.write_bytes(content)
    tmp.replace(target)


def _text(messages: list) -> str:
    """execd streams one message per line, without the newline."""
    return "".join(f"{message.text}\n" for message in messages)


async def run_in_sandbox(
    args: list[str],
    *,
    skill_dir: Path,
    output_dir: Path,
    env: dict[str, str],
    timeout_seconds: float,
    domains: Sequence[str],
) -> SkillRunResult:
    from opensandbox.exceptions import SandboxException

    skill_files = await asyncio.to_thread(_read_tree, skill_dir, settings.skill_max_package_bytes)
    outputs = await asyncio.to_thread(_read_outputs, output_dir)
    try:
        sandbox = await _create(timeout_seconds, domains)
    except SandboxException as exc:
        logger.warning("skill sandbox create failed: %s", type(exc).__name__)
        raise SkillSandboxError("skill sandbox is unavailable.") from exc
    try:
        await _upload(sandbox, skill_files, outputs)
        run = await _run(sandbox, args, env, timeout_seconds)
        if run.returncode is not None:
            await _download(sandbox, output_dir)
        return SkillRunResult(
            run.stdout, run.stderr, run.returncode, run.timed_out, sandbox_id=sandbox.id
        )
    except SandboxException as exc:
        logger.warning("skill sandbox run failed: %s", type(exc).__name__)
        raise SkillSandboxError("skill sandbox stopped before the command finished.") from exc
    finally:
        # A cancelled run must still delete its sandbox.
        await asyncio.shield(_discard(sandbox))


async def _create(timeout_seconds: float, domains: Sequence[str]) -> Sandbox:
    from opensandbox import Sandbox
    from opensandbox.config import ConnectionConfig

    connection = ConnectionConfig(
        api_key=settings.skill_sandbox_api_key or None,
        domain=settings.skill_sandbox_domain,
        use_server_proxy=settings.skill_sandbox_use_server_proxy,
        request_timeout=timedelta(seconds=timeout_seconds + 60),
    )
    return await Sandbox.create(
        settings.skill_sandbox_image,
        connection_config=connection,
        network_policy=network_policy(domains),
        resource={"cpu": settings.skill_sandbox_cpu, "memory": settings.skill_sandbox_memory},
        metadata={"app": "moldy", "kind": "execute_in_skill"},
        timeout=timedelta(seconds=timeout_seconds + _LIFETIME_MARGIN_SECONDS),
        ready_timeout=timedelta(seconds=60),
    )


async def _upload(
    sandbox: Sandbox, skill_files: list[tuple[str, bytes]], outputs: list[tuple[str, bytes]]
) -> None:
    from opensandbox.models.filesystem import WriteEntry

    entries = [WriteEntry(path=f"{SKILL_DIR}/{rel}", data=data) for rel, data in skill_files]
    entries += [WriteEntry(path=f"{OUTPUT_DIR}/{name}", data=data) for name, data in outputs]
    if entries:
        await sandbox.files.write_files(entries)
    # execd rejects a missing working directory; the stamp marks what the command wrote.
    prepared = await sandbox.commands.run(
        f"mkdir -p {SKILL_DIR} {OUTPUT_DIR} && touch {_STARTED} && "
        f"chown -R {SANDBOX_UID}:{SANDBOX_UID} /workspace"
    )
    if prepared.exit_code != 0:
        raise SkillSandboxError("skill sandbox could not prepare the workspace.")


async def _run(
    sandbox: Sandbox, args: list[str], env: dict[str, str], timeout_seconds: float
) -> SkillRunResult:
    from opensandbox.models.execd import RunCommandOpts

    started = asyncio.get_running_loop().time()
    # execd v1.1 takes shell text only; every argument is quoted.
    execution = await sandbox.commands.run(
        shlex.join(sandbox_argv(args)),
        opts=RunCommandOpts(
            working_directory=SKILL_DIR,
            timeout=timedelta(seconds=timeout_seconds),
            uid=SANDBOX_UID,
            gid=SANDBOX_UID,
            envs=env,
        ),
    )
    elapsed = asyncio.get_running_loop().time() - started
    code = execution.exit_code
    timed_out = (code is None or code < 0) and elapsed >= timeout_seconds
    stderr = _text(execution.logs.stderr)
    if execution.error is not None and code is None and not timed_out:
        stderr += f"\n[{execution.error.name}] {execution.error.value}"
    return SkillRunResult(
        stdout=_text(execution.logs.stdout),
        stderr=stderr,
        returncode=None if timed_out else code,
        timed_out=timed_out,
    )


async def _download(sandbox: Sandbox, output_dir: Path) -> None:
    listing = await sandbox.commands.run(
        f"find {OUTPUT_DIR} -maxdepth 1 -type f -newer {_STARTED} -printf '%f\\t%s\\n'"
    )
    total = 0
    for line in _text(listing.logs.stdout).splitlines():
        name, _, size = line.rpartition("\t")
        if not _safe_name(name) or not size.isdigit():
            continue
        total += int(size)
        if int(size) > MAX_OUTPUT_FILE_BYTES or total > MAX_OUTPUT_TOTAL_BYTES:
            logger.warning("skill sandbox output over the size limit was not copied")
            continue
        content = await sandbox.files.read_bytes(f"{OUTPUT_DIR}/{name}")
        await asyncio.to_thread(_write_output, output_dir, name, content)


async def _discard(sandbox: Sandbox) -> None:
    try:
        await sandbox.kill()
    except Exception:  # noqa: BLE001 - the server expires it after its lifetime
        logger.warning("skill sandbox delete failed: %s", sandbox.id, exc_info=True)
    try:
        await sandbox.close()
    except Exception:  # noqa: BLE001
        logger.debug("skill sandbox close failed", exc_info=True)
