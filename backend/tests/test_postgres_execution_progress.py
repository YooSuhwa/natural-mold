"""Durable structural progress receipts for forcibly terminated pytest children."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

from postgres_progress_receipt import (  # noqa: E402
    ProgressReceiptError,
    load_progress_receipt,
)

_SECRET = "postgresql://runner:progress-secret@127.0.0.1/private"


def _child_environment(receipt: Path) -> dict[str, str]:
    allowed = {"PATH", "TMPDIR", "LANG", "LC_ALL", "TZ", "HOME", "USER", "LOGNAME"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update(
        {
            "MOLDY_DISABLE_ENV_FILE": "true",
            "PYTHON_DOTENV_DISABLED": "1",
            "MOLDY_PG_TEST_RECEIPT": str(receipt),
            "DUMMY_PROGRESS_SECRET": _SECRET,
        }
    )
    return environment


def _pytest_command(test_file: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--rootdir",
        str(BACKEND_ROOT),
        "-p",
        "tests.postgres_execution_plugin",
        str(test_file),
    ]


def _wait_for_call_phase(receipt: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if receipt.is_file():
            try:
                progress = load_progress_receipt(receipt, require_finished=False)["progress"]
            except ProgressReceiptError:
                # The child is still publishing this poll's receipt; the final load stays strict.
                time.sleep(0.02)
                continue
            if progress["active_phase"] == "call":
                return
        time.sleep(0.02)
    raise AssertionError("pytest child did not publish call-phase progress")


def test_completed_child_preserves_normal_receipt_contract(tmp_path: Path) -> None:
    test_file = tmp_path / "test_progress_complete.py"
    receipt = tmp_path / "complete-receipt.json"
    test_file.write_text("def test_complete():\n    assert True\n", encoding="utf-8")

    child = subprocess.run(  # noqa: S603 - fixed interpreter and pytest arguments
        _pytest_command(test_file),
        cwd=BACKEND_ROOT,
        env=_child_environment(receipt),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert child.returncode == 0, child.stderr
    payload = load_progress_receipt(receipt, require_finished=True)
    assert payload["selected_node_ids"] == payload["executed_node_ids"]
    assert payload["failed_node_ids"] == []
    assert payload["skipped_node_ids"] == []
    assert payload["progress"]["state"] == "finished"
    assert payload["progress"]["completed_node_ids"] == payload["selected_node_ids"]
    assert payload["resource_receipt"] is not None


def test_terminated_child_retains_atomic_call_progress_without_values(tmp_path: Path) -> None:
    test_file = tmp_path / "test_progress_blocked.py"
    receipt = tmp_path / "blocked-receipt.json"
    test_file.write_text(
        "import os\n"
        "import time\n"
        "def test_blocked():\n"
        "    print(os.environ['DUMMY_PROGRESS_SECRET'])\n"
        "    print(\"SELECT * FROM credentials WHERE value='progress-secret'\")\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )
    child = subprocess.Popen(  # noqa: S603 - fixed interpreter and pytest arguments
        _pytest_command(test_file),
        cwd=BACKEND_ROOT,
        env=_child_environment(receipt),
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_call_phase(receipt)
        child.terminate()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)

    payload = load_progress_receipt(receipt, require_finished=False)
    progress = payload["progress"]
    assert child.returncode != 0
    assert progress["state"] == "running"
    assert progress["active_node_id"] is not None
    assert progress["active_phase"] == "call"
    assert progress["active_node_id"] not in progress["completed_node_ids"]
    serialized = json.dumps(payload, sort_keys=True)
    assert "progress-secret" not in serialized
    assert "SELECT" not in serialized
    assert "credentials" not in serialized


def test_progress_loader_rejects_symlink_receipt(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.symlink_to(target)

    with pytest.raises(ProgressReceiptError, match="unsafe_progress_receipt"):
        load_progress_receipt(receipt, require_finished=False)
