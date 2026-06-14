from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.skill_executor_audit import (
    record_credential_audits,
    record_sandbox_denial,
)
from app.config import settings
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)
from app.tools.risk import attach_tool_risk, execute_in_skill_risk

_SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SHELL_DEFAULT_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*?)\}")
_SHELL_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_DEFAULT_SKILL_TIMEOUT_SECONDS = 30.0
_MAX_SKILL_TIMEOUT_SECONDS = 420.0


def _expand_shell_vars(value: str, *, env: dict[str, str], local_vars: dict[str, str]) -> str:
    """Expand the small shell-var subset used in k-skill curl examples."""

    def lookup(name: str) -> str:
        return local_vars.get(name) or env.get(name, "")

    def replace_default(match: re.Match[str]) -> str:
        name = match.group(1)
        fallback = match.group(2)
        return lookup(name) or fallback

    expanded = _SHELL_DEFAULT_RE.sub(replace_default, value)

    def replace_var(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return lookup(name)

    return _SHELL_VAR_RE.sub(replace_var, expanded)


def _prepare_skill_subprocess_args(
    command: str, *, resolved: Path, env: dict[str, str]
) -> tuple[list[str] | None, str | None]:
    """Convert an LLM-provided command into argv for the allowed executables.

    We intentionally do not run a shell here. k-skill docs frequently use
    ``BASE=...`` followed by ``curl "${BASE}/..."``; supporting that narrow
    shape is enough without opening arbitrary shell command execution.
    """

    try:
        raw_args = [arg for arg in shlex.split(command) if arg.strip()]
    except ValueError as exc:
        return None, f"Error: invalid command syntax: {exc}"

    local_vars: dict[str, str] = {}
    index = 0
    while index < len(raw_args) and _SHELL_ASSIGNMENT_RE.match(raw_args[index]):
        name, raw_value = raw_args[index].split("=", 1)
        local_vars[name] = _expand_shell_vars(raw_value, env=env, local_vars=local_vars)
        index += 1

    args = [_expand_shell_vars(arg, env=env, local_vars=local_vars) for arg in raw_args[index:]]
    if not args:
        return None, "Error: command must start with python, node, or curl."

    executable = Path(args[0]).name
    if executable == "python":
        # 스크립트 경로가 스킬 디렉토리 하위인지 검증
        if len(args) > 1 and not args[1].startswith("-"):
            script_path = (resolved / args[1]).resolve()
            if not script_path.is_relative_to(resolved):
                return None, "Error: script must be within the skill directory."
        args[0] = sys.executable
        return args, None

    if executable == "node":
        if len(args) < 2 or args[1].startswith("-"):
            return None, "Error: node command must be `node scripts/<file>.cjs ...`."
        script_path = (resolved / args[1]).resolve()
        if not script_path.is_relative_to(resolved):
            return None, "Error: node script must be within the skill directory."
        if script_path.suffix.lower() not in {".js", ".cjs", ".mjs"}:
            return None, "Error: node script must use .js, .cjs, or .mjs."
        node_binary = _resolve_node_binary()
        if node_binary is None:
            return None, "Error: node executable is not available for skill execution."
        args[0] = node_binary
        return args, None

    if executable == "curl":
        args[0] = "curl"
        return args, None

    return None, "Error: only python, node, or curl commands are allowed."


def _resolve_node_binary() -> str | None:
    configured = settings.skill_node_binary.strip() or "node"
    if Path(configured).is_absolute():
        path = Path(configured)
        return str(path) if path.exists() and os.access(path, os.X_OK) else None
    return shutil.which(configured, path=os.environ.get("PATH"))


def _resolve_skill_node_modules() -> Path | None:
    raw = settings.skill_node_modules_dir.strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    return path if path.is_dir() else None


def _skill_timeout_seconds(descriptor: SkillRuntimeDescriptor) -> float:
    profile = descriptor.execution_profile or {}
    raw = profile.get("timeout_seconds")
    if raw is None:
        return _DEFAULT_SKILL_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_SKILL_TIMEOUT_SECONDS
    if seconds <= 0:
        return _DEFAULT_SKILL_TIMEOUT_SECONDS
    return min(seconds, _MAX_SKILL_TIMEOUT_SECONDS)


def _requires_network(descriptor: SkillRuntimeDescriptor) -> bool:
    profile = descriptor.execution_profile or {}
    return profile.get("requires_network") is True


def _create_skill_execute_tool(ctx: SkillToolContext) -> BaseTool:
    """스킬 디렉토리에서 Python 스크립트를 실행하는 도구를 생성.

    ADR-017 Slice E refactor — the tool now closes over a
    ``SkillToolContext`` (output_dir + thread_id + runtime_root + slug
    descriptor map). Stage 1 preserves the legacy validation surface;
    stage 2 swaps ``runtime_root`` to the per-thread location and adds
    "unknown slug" rejection. The closure shape was deliberately
    chosen to avoid an argument explosion on the inner ``execute_in_skill``
    body — see Bezos OI-3.
    """

    output_dir = ctx.output_dir
    api_file_prefix = f"/api/conversations/{ctx.thread_id}/files/" if ctx.thread_id else ""
    _path_re = re.compile(re.escape(str(output_dir)) + r"/([^\s\n]+)") if api_file_prefix else None

    async def execute_in_skill(skill_directory: str, command: str) -> str:
        """스킬 디렉토리에서 Python 스크립트를 실행합니다.

        Args:
            skill_directory: 스킬 디렉토리의 가상 경로
                (예: /runtime/<thread_id>/skills/<slug>/).
            command: 실행할 명령어 (예: python scripts/mark_seat.py search 이상윤)
        """
        # ``Path(skill_directory).name`` extracts the final segment
        # regardless of leading slashes / trailing slashes — the LLM may
        # pass any of ``/skills/<slug>``, ``<slug>``, ``<slug>/``.
        requested_slug = Path(skill_directory.strip("/")).name
        descriptor = ctx.descriptors.get(requested_slug)
        if descriptor is None:
            # Selected-skill mount (Spec §9) — even if the slug resolves
            # to a real on-disk directory, refuse it when it wasn't
            # attached to this agent. This is the regression guard for
            # the legacy broad ``/skills/`` mount that leaked siblings.
            return f"Error: skill not attached to this agent: {requested_slug}"

        # Resolve against the per-thread runtime root so traversal
        # attempts (``../``, absolute paths like ``/etc/passwd``) all
        # fail the ``is_relative_to`` check below.
        resolved = descriptor.runtime_storage_path.resolve()
        if not resolved.is_relative_to(ctx.runtime_root.resolve()) or not resolved.is_dir():
            return f"Error: invalid skill directory: {skill_directory}"

        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        out = str(output_dir)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
            "PYTHONPATH": str(resolved),
            "HOME": str(resolved),
            "SKILL_OUTPUT_DIR": out,
            "OUTPUTS_DIR": out,
        }
        node_modules = _resolve_skill_node_modules()
        if node_modules is not None:
            env["NODE_PATH"] = str(node_modules)
        for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            value = os.environ.get(key)
            if value:
                env[key] = value
        # Slice E Stage 3 — credential env injection (Spec §8.2).
        # ``descriptor.credential_bindings`` is populated by
        # ``build_skill_runtime_context`` at agent build time so the
        # hot path here only does an in-memory copy; no decrypt, no DB.
        # ``env_map`` shape: ``{credential_field_name: env_var_name}``.
        injected_env: dict[str, str] = {}
        injected_credentials: list[tuple[str, ResolvedCredential]] = []
        for requirement_key, rc in descriptor.credential_bindings.items():
            injected = False
            for field, env_name in rc.env_map.items():
                value = rc.decrypted.get(field)
                if value is None:
                    continue
                env[env_name] = value
                injected_env[env_name] = value
                injected = True
            if injected:
                injected_credentials.append((requirement_key, rc))

        args, error = _prepare_skill_subprocess_args(command, resolved=resolved, env=env)
        if error is not None or args is None:
            return error or "Error: invalid command."
        timeout_seconds = _skill_timeout_seconds(descriptor)
        executable = Path(args[0]).name
        if executable == "curl" and not _requires_network(descriptor):
            await record_sandbox_denial(
                ctx,
                descriptor,
                reason_code="undeclared_network",
                executable=executable,
            )
            return "Error: network access requires execution_profile.requires_network=true."
        await record_credential_audits(
            ctx,
            descriptor,
            injected_credentials=injected_credentials,
            executable=executable,
            timeout_seconds=timeout_seconds,
        )

        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(resolved),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Error: script execution timed out ({timeout_seconds:g}s)."
        except asyncio.CancelledError:
            # run cancel(Stop)/worker shutdown 이 이 await 를 취소하면 timeout
            # kill 경로도 함께 취소되어 subprocess 가 고아로 남는다 — 즉시
            # 종료시켜 취소된 대화 디렉토리에 출력이 계속 쌓이는 것을 막고
            # 취소를 전파한다.
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise

        result = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")
            result += f"\nSTDERR: {err}"

        # IMAGE: 절대경로 → API URL 자동 변환
        if _path_re and str(output_dir) in result:
            result = _path_re.sub(lambda m: api_file_prefix + m.group(1), result)

        # 출력 파일 수집
        def _collect_outputs() -> list[str]:
            if output_dir.exists():
                return [f.name for f in output_dir.iterdir() if f.is_file()]
            return []

        files = await asyncio.to_thread(_collect_outputs)
        if files:
            result += "\n\nOUTPUT_FILES: " + ", ".join(files)

        # Slice E Stage 4 — redact credential values that the script
        # may have echoed back through stdout/stderr (debug prints,
        # uncaught exceptions, etc.). The mapped env dict is the
        # authoritative set of values the runtime injected for this
        # skill; everything else passes through untouched.
        if injected_env:
            from app.marketplace.redaction import redact_credential_values

            result = redact_credential_values(result, injected_env)

        return result

    tool = StructuredTool.from_function(
        coroutine=execute_in_skill,
        name="execute_in_skill",
        description=(
            "Execute an allowed Python or Node script inside a skill directory. "
            "Use this when SKILL.md instructs you to run a script. "
            "Output files (images etc.) will be in OUTPUT_FILES."
        ),
    )
    return attach_tool_risk(tool, execute_in_skill_risk())
