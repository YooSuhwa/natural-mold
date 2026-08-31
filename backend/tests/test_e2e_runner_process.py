"""Port and process ownership tests for the isolated E2E runner."""

from __future__ import annotations

import io
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_runner_process as process_module  # noqa: E402
from e2e_runner_process import (  # noqa: E402
    OwnedProcessInterrupted,
    assert_ports_available,
    ports_have_no_listener,
    run_owned_process,
    start_owned_process,
)
from postgres_manifest_io import RunnerInterrupted  # noqa: E402


def test_port_preflight_preserves_foreign_listener() -> None:
    """Given a foreign listener, when checked, then it remains bound and startup fails."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    try:
        with pytest.raises(RuntimeError, match="lane_port_unavailable"):
            assert_ports_available((port,))
        listener.listen()
        assert ports_have_no_listener((port,)) is False
    finally:
        listener.close()
    assert ports_have_no_listener((port,)) is True


def test_owned_process_captures_exit_and_reaps_group(tmp_path: Path) -> None:
    """Given a child process, when it exits, then output and group cleanup are observed."""
    result = run_owned_process(
        [sys.executable, "-c", "print('owned')"],
        cwd=tmp_path,
        env={"PATH": ""},
        stdout_path=tmp_path / "stdout",
        stderr_path=tmp_path / "stderr",
    )

    assert result.returncode == 0
    assert result.process_group_stopped is True
    assert (tmp_path / "stdout").read_text().strip() == "owned"


def test_interrupted_process_fail_closes_when_group_stop_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given a stuck process group, when interrupted, then cleanup cannot report green."""

    class StuckProcess:
        pid = 987654

        def wait(self) -> int:
            raise RunnerInterrupted(2)

    monkeypatch.setattr(
        process_module.subprocess, "Popen", lambda *_args, **_kwargs: StuckProcess()
    )
    monkeypatch.setattr(process_module, "stop_process", lambda _process: False)

    with pytest.raises(OwnedProcessInterrupted) as captured:
        run_owned_process(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            env={"PATH": ""},
            stdout_path=tmp_path / "stdout",
            stderr_path=tmp_path / "stderr",
        )

    assert captured.value.signal_number == 2
    assert captured.value.process_group_stopped is False


def test_start_owned_process_closes_log_on_interruption(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = io.BytesIO()
    monkeypatch.setattr(process_module.Path, "open", lambda *_args, **_kwargs: log)

    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise RunnerInterrupted(2)

    monkeypatch.setattr(process_module.subprocess, "Popen", interrupt)

    with pytest.raises(RunnerInterrupted):
        start_owned_process([], cwd=tmp_path, env={}, log_path=tmp_path / "log")

    assert log.closed is True
