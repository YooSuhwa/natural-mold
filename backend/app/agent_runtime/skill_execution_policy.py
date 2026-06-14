from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Final

from app.config import settings
from app.marketplace.skill_runtime import SkillRuntimeDescriptor

_SHELL_ASSIGNMENT_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SHELL_DEFAULT_RE: Final = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*?)\}")
_SHELL_VAR_RE: Final = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_DEFAULT_SKILL_TIMEOUT_SECONDS: Final = 30.0
_MAX_SKILL_TIMEOUT_SECONDS: Final = 420.0


def _expand_shell_vars(value: str, *, env: dict[str, str], local_vars: dict[str, str]) -> str:
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


def sandbox_denial_for_prepare_error(command: str, error: str) -> tuple[str, str] | None:
    executable = _command_executable(command)
    if "only python, node, or curl commands are allowed" in error:
        return ("unsupported_executable", executable)
    if "must be within the skill directory" in error:
        return ("path_traversal", executable)
    return None


def skill_timeout_policy_error(descriptor: SkillRuntimeDescriptor) -> str | None:
    profile = descriptor.execution_profile or {}
    raw = profile.get("timeout_seconds")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return "Error: timeout_seconds must be a number."
    if seconds <= 0 or seconds > _MAX_SKILL_TIMEOUT_SECONDS:
        return f"Error: timeout_seconds must be between 0 and {_MAX_SKILL_TIMEOUT_SECONDS:g}."
    return None


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


def _command_executable(command: str) -> str:
    try:
        raw_args = [arg for arg in shlex.split(command) if arg.strip()]
    except ValueError:
        return "unknown"
    index = 0
    while index < len(raw_args) and _SHELL_ASSIGNMENT_RE.match(raw_args[index]):
        index += 1
    if index >= len(raw_args):
        return "unknown"
    return Path(raw_args[index]).name
