"""Bounded, private subprocess lifetimes for the multi-worker acceptance stack."""

from __future__ import annotations

import contextlib
import errno
import os
import re
import select
import signal
import socket
import subprocess
import sys
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


def _exited(process: subprocess.Popen[bytes], timeout: float) -> bool:
    """Observe an exit without releasing the leader's PID before group cleanup."""
    if hasattr(select, "kqueue"):
        # Darwin does not expose waitid(WNOWAIT) in CPython. Registration after
        # exit returns ESRCH, while observation of a running child returns NOTE_EXIT.
        change = select.kevent(
            process.pid,
            filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT,
        )
        with contextlib.closing(select.kqueue()) as queue:
            try:
                events = queue.control([change], 1, timeout)
            except ProcessLookupError:
                return True
        if not events:
            return False
        event = events[0]
        if event.flags & select.KQ_EV_ERROR and event.data != errno.ESRCH:
            raise OSError(event.data, "Cannot observe managed child exit")
        return True
    waitid = getattr(os, "waitid", None)
    if waitid is None:
        raise RuntimeError("Process-group cleanup needs kqueue or waitid")
    deadline = time.monotonic() + timeout
    while waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
    return True


def _live_group(pgid: int) -> bool:
    if sys.platform == "linux":
        # Minimal Python images omit procps. Read state only for our group.
        for task in Path("/proc").iterdir():
            if not task.name.isdecimal():
                continue
            try:
                if os.getpgid(int(task.name)) != pgid:
                    continue
                # The comm field is parenthesized and can itself contain spaces or ')'.
                state, _, group, _ = (task / "stat").read_bytes().rsplit(b") ", 1)[1].split(None, 3)
            except (FileNotFoundError, ProcessLookupError):
                continue
            if int(group) == pgid and state not in {b"Z", b"X"}:
                return True
        return False
    result = subprocess.run(  # noqa: S603 - fixed BSD ps query limited to our numeric PGID
        ["/bin/ps", "-g", str(pgid), "-o", "pgid=,stat="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return False
    if result.returncode:
        raise RuntimeError("Could not verify the managed process group")
    for row in result.stdout.splitlines():
        group, status = row.split()
        if int(group) == pgid and not status.startswith(("Z", "X")):
            return True
    return False


def _signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> bool:
    if process.returncode is not None:
        raise RuntimeError("Managed group leader was reaped before cleanup")
    if not _live_group(process.pid):
        return False
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        # On Darwin a group containing only zombies can return EPERM for all signals.
        if _live_group(process.pid):
            raise
        return False
    return True


@contextlib.contextmanager
def managed(
    name: str, args: list[str], cwd: Path, env: dict[str, str], logs: Path
) -> Iterator[tuple[subprocess.Popen[bytes], Path]]:
    output = logs / f"{name}.log"
    with output.open("wb") as f:
        process = subprocess.Popen(  # noqa: S603 - acceptance runner supplies argv without a shell
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
            # The unreaped leader reserves its PID even after an early exit. Descendants
            # of Node/Playwright can continue in the group until the last signal.
            try:
                _signal_group(process, signal.SIGTERM)
                _exited(process, 12)
            finally:
                try:
                    if _signal_group(process, signal.SIGKILL):
                        deadline = time.monotonic() + 10
                        while _live_group(process.pid):
                            if time.monotonic() >= deadline:
                                raise RuntimeError(f"{name} group survived SIGKILL")
                            time.sleep(0.05)
                finally:
                    process.wait(timeout=10)


def command(
    name: str,
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    logs: Path,
    timeout: int = 300,
) -> None:
    with managed(name, args, cwd, env, logs) as (proc, logfile):
        if not _exited(proc, timeout):
            raise RuntimeError(f"{name} exceeded {timeout}s")
    if proc.returncode:
        raise RuntimeError(f"{name} exited {proc.returncode}\n{scrub_logs(logfile, env)}")


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
    while not _exited(process, 0) and time.monotonic() < deadline:
        if url:
            try:
                # run.py and stack_workers.py supply their freshly allocated loopback HTTP ports.
                with urlopen(url, timeout=2) as response:  # noqa: S310
                    if response.status == 200:
                        return
            except (URLError, HTTPError, TimeoutError, ConnectionError):
                pass
        elif marker in logfile.read_text(errors="replace"):
            return
        time.sleep(0.2)
    state = "exited" if _exited(process, 0) else "running"
    raise RuntimeError(f"{logfile.stem} not ready ({state})\n{scrub_logs(logfile, env)}")
