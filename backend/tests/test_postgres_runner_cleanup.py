"""Adversarial cleanup contracts for the PostgreSQL lifecycle runner."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import postgres_runner_cleanup  # noqa: E402
from postgres_manifest_io import cleanup_receipt_succeeded  # noqa: E402
from postgres_runner_cleanup import CleanupContext, cleanup_scenario_resources  # noqa: E402
from postgres_runner_runtime import OwnedContainer  # noqa: E402


class _SecretCleanupFailure(BaseException):
    """Adversarial teardown interruption carrying content that must not escape."""


@pytest.mark.parametrize("failure", ["container", "verify", "label", "root", "docker", "process"])
def test_cleanup_continues_after_each_destructive_step_raises(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given every cleanup adapter records its call and one raises BaseException.
    calls: list[str] = []

    def result[T](name: str, value: T) -> T:
        calls.append(name)
        if failure == name:
            raise _SecretCleanupFailure("credential-canary")
        return value

    monkeypatch.setattr(
        postgres_runner_cleanup,
        "cleanup_container",
        lambda _owner: result("container", True),
    )
    monkeypatch.setattr(
        postgres_runner_cleanup,
        "verify_container_absence",
        lambda _owner: result("verify", (True, True, True)),
    )
    monkeypatch.setattr(
        postgres_runner_cleanup,
        "run_command",
        lambda argv, **_kwargs: result("label", subprocess.CompletedProcess(argv, 0, "", "")),
    )
    monkeypatch.setattr(
        postgres_runner_cleanup,
        "cleanup_run_root",
        lambda _path, _device, _inode: result("root", True),
    )
    monkeypatch.setattr(
        postgres_runner_cleanup, "docker_ids", lambda: result("docker", {"foreign"})
    )
    monkeypatch.setattr(
        postgres_runner_cleanup, "process_groups_stopped", lambda: result("process", True)
    )
    run_root = tmp_path / "run"
    run_root.mkdir()
    metadata = run_root.stat()
    context = CleanupContext(
        owner=OwnedContainer("owner", "run", "container"),
        owner_token="owner",
        run_root=run_root,
        root_device=metadata.st_dev,
        root_inode=metadata.st_ino,
        before_ids={"foreign"},
    )

    # When the fail-closed cleanup boundary executes every step.
    receipt = cleanup_scenario_resources(context)

    # Then later cleanup is never skipped, failure remains conservative, and text is redacted.
    assert calls == ["container", "verify", "label", "root", "docker", "process"]
    assert cleanup_receipt_succeeded(receipt) is False
    assert "credential-canary" not in json.dumps(receipt)
