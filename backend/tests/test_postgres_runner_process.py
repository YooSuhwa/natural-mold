"""Process-group failure bookkeeping for the PostgreSQL runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import postgres_runner_process  # noqa: E402


class _InterruptedProcess:
    pid = 424242
    returncode = -1

    def communicate(self, *, timeout: float) -> tuple[str, str]:
        del timeout
        raise KeyboardInterrupt


@pytest.mark.parametrize("stop_raises", [False, True])
def test_run_command_records_unkillable_group_after_base_exception(
    stop_raises: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a child interrupted by BaseException whose process group remains alive.
    postgres_runner_process.start_process_scope()
    monkeypatch.setattr(
        postgres_runner_process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _InterruptedProcess(),
    )

    def stop_group(_process: _InterruptedProcess) -> None:
        if stop_raises:
            raise KeyboardInterrupt

    monkeypatch.setattr(postgres_runner_process, "_stop_process_group", stop_group)
    monkeypatch.setattr(postgres_runner_process, "_process_group_absent", lambda _group: False)

    try:
        # When the command boundary handles the interruption.
        with pytest.raises(KeyboardInterrupt):
            postgres_runner_process.run_command(["owned-child"])

        # Then active tracking is cleared but durable failure bookkeeping remains false-closed.
        assert postgres_runner_process.process_groups_stopped() is False
    finally:
        postgres_runner_process.start_process_scope()
