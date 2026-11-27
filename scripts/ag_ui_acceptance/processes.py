"""Bounded, private subprocess lifetimes for the multi-worker acceptance stack."""

from __future__ import annotations

import contextlib
import os
import re
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def ports(count: int) -> list[int]:
    with contextlib.ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in sockets]


def scrub_logs(logfile: Path, env: dict[str, str]) -> str:
    """A short local diagnostic; credentials and authorization state never enter the receipt."""
    if not logfile.exists():
        return ""
    with logfile.open("rb") as f:
        f.seek(max(0, logfile.stat().st_size - 12000))
        logs = f.read().decode("utf-8", errors="replace")
    private = [
        v
        for k, v in env.items()
        if any(
            t in k
            for t in (
                "KEY",
                "PASSWORD",
                "SECRET",
                "TOKEN",
                "DATABASE_URL",
                "OBSERVER_URL",
            )
        )
        and len(v) >= 4
    ]
    for value in sorted(private, key=len, reverse=True):
        logs = logs.replace(value, "[redacted]")
    logs = re.sub(r"postgresql[^\s]+", "[database]", logs)
    logs = re.sub(r"eyJ[A-Za-z0-9_.-]{20,}", "[token]", logs)
    logs = re.sub(
        r"(?im)^.*(?:authorization:|set-cookie:|cookie:|access_token|refresh_token|password|moldy_[arc]t=).*$",
        "[redacted]",
        logs,
    )
    return logs[-7000:]


@contextlib.contextmanager
def managed(
    name: str, args: list[str], cwd: Path, env: dict[str, str], logs: Path
) -> Iterator[tuple[subprocess.Popen[bytes], Path]]:
    output = logs / f"{name}.log"
    with output.open("wb") as f:
        process = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdout=f,
            stderr=f,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            yield process, output
        finally:
            # Commands create descendants (Node compiler workers, Playwright browser). Terminate
            # the owned group even if its leader exited before the probe command returned.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def command(
    name: str,
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    logs: Path,
    timeout: int = 300,
) -> None:
    with managed(name, args, cwd, env, logs) as (proc, logfile):
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{name} exceeded {timeout}s") from None
        if code:
            raise RuntimeError(f"{name} exited {code}\n{scrub_logs(logfile, env)}")


def wait_ready(
    process: subprocess.Popen[bytes],
    logfile: Path,
    env: dict[str, str],
    *,
    url: str | None = None,
    marker: str = "",
    timeout: int = 120,
) -> None:
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        if url:
            try:
                with urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (URLError, HTTPError, TimeoutError, ConnectionError):
                pass
        elif marker in logfile.read_text(errors="replace"):
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"{logfile.stem} not ready (exit {process.poll()})\n{scrub_logs(logfile, env)}"
    )
