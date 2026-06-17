from __future__ import annotations

import ipaddress
import os
import re
import shlex
import shutil
import socket
import sys
from pathlib import Path
from typing import Final
from urllib.parse import ParseResult, unquote, urlparse

from app.config import settings
from app.marketplace.skill_runtime import SkillRuntimeDescriptor

_SHELL_ASSIGNMENT_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SHELL_DEFAULT_RE: Final = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*?)\}")
_SHELL_VAR_RE: Final = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_DEFAULT_SKILL_TIMEOUT_SECONDS: Final = 30.0
_MAX_SKILL_TIMEOUT_SECONDS: Final = 420.0
_SAFE_CURL_FLAGS: Final[frozenset[str]] = frozenset({"-f", "--fail", "-s", "-S", "-sS"})
_SAFE_CURL_SHORT_FLAGS: Final[frozenset[str]] = frozenset({"f", "s", "S"})
_SAFE_CURL_FLAGS_WITH_VALUE: Final[frozenset[str]] = frozenset({"--connect-timeout", "--max-time"})
_BLOCKED_CURL_HOSTS: Final = frozenset({"localhost", "metadata.google.internal", "metadata"})


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
        if len(args) < 2 or args[1].startswith("-"):
            return None, "Error: python command must be `python scripts/<file>.py ...`."
        script_path = (resolved / args[1]).resolve()
        if not script_path.is_relative_to(resolved):
            return None, "Error: script must be within the skill directory."
        if script_path.suffix.lower() != ".py":
            return None, "Error: python script must use .py."
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
        curl_error = _curl_policy_error(args, resolved=resolved)
        if curl_error is not None:
            return None, curl_error
        args[0] = "curl"
        return args, None

    return None, "Error: only python, node, or curl commands are allowed."


def sandbox_denial_for_prepare_error(command: str, error: str) -> tuple[str, str] | None:
    executable = _command_executable(command)
    if "only python, node, or curl commands are allowed" in error:
        return ("unsupported_executable", executable)
    if "python command must be" in error:
        return ("inline_python", executable)
    if "must be within the skill directory" in error:
        return ("path_traversal", executable)
    if "curl" in error:
        return ("curl_url_policy", executable)
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


def _curl_policy_error(args: list[str], *, resolved: Path) -> str | None:
    urls: list[str] = []
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in _SAFE_CURL_FLAGS or _is_safe_curl_short_flag_group(arg):
            index += 1
            continue
        if arg in _SAFE_CURL_FLAGS_WITH_VALUE:
            if index + 1 >= len(args) or args[index + 1].startswith("-"):
                return "Error: curl option requires a value."
            index += 2
            continue
        if arg.startswith("-"):
            return "Error: curl option is not allowed."
        urls.append(arg)
        index += 1
    if len(urls) != 1:
        return "Error: curl command must contain exactly one URL."
    url_error, resolve_args = _curl_url_policy_result(urls[0], resolved=resolved)
    if url_error is not None:
        return url_error
    if resolve_args:
        args[1:1] = resolve_args
    return None


def _is_safe_curl_short_flag_group(arg: str) -> bool:
    return (
        arg.startswith("-")
        and not arg.startswith("--")
        and len(arg) > 2
        and all(flag in _SAFE_CURL_SHORT_FLAGS for flag in arg[1:])
    )


def _curl_url_policy_result(raw_url: str, *, resolved: Path) -> tuple[str | None, list[str]]:
    parsed = urlparse(raw_url)
    if parsed.scheme == "file":
        if parsed.hostname not in {None, "", "localhost"}:
            return "Error: curl URL host is not allowed.", []
        file_path = Path(unquote(parsed.path)).resolve()
        if not file_path.is_relative_to(resolved):
            return "Error: curl URL host is not allowed.", []
        return None, []
    if parsed.scheme not in {"http", "https"}:
        return "Error: curl URL must use http or https.", []
    host = parsed.hostname
    if host is None or not host.strip():
        return "Error: curl URL host is required.", []
    if _blocked_curl_host(host):
        return "Error: curl URL host is not allowed.", []
    port = _curl_url_port(parsed)
    if port is None:
        return "Error: curl URL port is invalid.", []
    if _literal_ip_address(host) is not None:
        return None, []
    resolved_ip = _resolve_public_curl_host(_normalized_curl_host(host), port)
    if resolved_ip is None:
        return "Error: curl URL host is not allowed.", []
    return None, ["--resolve", f"{_normalized_curl_host(host)}:{port}:{resolved_ip}"]


def _curl_url_port(parsed: ParseResult) -> int | None:
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None:
        return port
    if parsed.scheme == "https":
        return 443
    return 80


def _blocked_curl_host(host: str) -> bool:
    normalized = _normalized_curl_host(host)
    if normalized in _BLOCKED_CURL_HOSTS or normalized.endswith(".localhost"):
        return True
    address = _literal_ip_address(normalized)
    if address is None:
        return False
    return _blocked_ip_address(address)


def _normalized_curl_host(host: str) -> str:
    return host.strip("[]").lower().rstrip(".")


def _literal_ip_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(_normalized_curl_host(host))
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_aton(_normalized_curl_host(host)))
    except OSError:
        return None


def _resolve_public_curl_host(host: str, port: int) -> str | None:
    try:
        results = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    for item in results:
        sockaddr = item[4]
        if not sockaddr:
            continue
        address = ipaddress.ip_address(str(sockaddr[0]))
        if _blocked_ip_address(address):
            return None
        return f"[{address}]" if address.version == 6 else str(address)
    return None


def _blocked_ip_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
