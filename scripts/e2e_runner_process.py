"""Owned process-group and fixed-port primitives for the E2E runner."""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from postgres_manifest_io import RunnerInterrupted

PORT_BIND_RETRY_SECONDS: Final = 5.0
PORT_BIND_RETRY_INTERVAL_SECONDS: Final = 0.05


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    process_group_stopped: bool


@dataclass(frozen=True, slots=True)
class OwnedProcessInterrupted(BaseException):
    signal_number: int | None
    process_group_stopped: bool


def assert_ports_available(
    ports: tuple[int, ...], *, timeout_seconds: float = PORT_BIND_RETRY_SECONDS
) -> None:
    if not ports_have_no_listener(ports):
        raise RuntimeError("lane_port_unavailable")
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        bind_error = _bind_ports_once(ports)
        if bind_error is None:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("lane_port_unavailable") from bind_error
        time.sleep(PORT_BIND_RETRY_INTERVAL_SECONDS)


def _bind_ports_once(ports: tuple[int, ...]) -> OSError | None:
    sockets: list[socket.socket] = []
    try:
        for port in ports:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(listener)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen()
    except OSError as error:
        return error
    finally:
        for listener in sockets:
            listener.close()
    return None


def ports_have_no_listener(ports: tuple[int, ...]) -> bool:
    for port in ports:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.1)
        try:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return False
        finally:
            probe.close()
    return True


def process_group_absent(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def stop_process(process: subprocess.Popen[bytes]) -> bool:
    if process_group_absent(process.pid):
        return True
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not process_group_absent(process.pid):
        time.sleep(0.02)
    if not process_group_absent(process.pid):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=2)
    return process_group_absent(process.pid)


def run_owned_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> ProcessResult:
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(  # noqa: S603 - canonical runner owns and validates each argv
            argv,
            cwd=cwd,
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        try:
            returncode = process.wait()
        except BaseException as error:
            stopped = stop_process(process)
            signal_number = error.signal_number if isinstance(error, RunnerInterrupted) else None
            raise OwnedProcessInterrupted(signal_number, stopped) from error
    return ProcessResult(returncode, stop_process(process))


def start_owned_process(
    argv: list[str], *, cwd: Path, env: dict[str, str], log_path: Path
) -> tuple[subprocess.Popen[bytes], IO[bytes]]:
    log_file = log_path.open("wb")
    try:
        process = subprocess.Popen(  # noqa: S603 - canonical runner owns and validates each argv
            argv,
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except BaseException:
        log_file.close()
        raise
    return process, log_file
