"""Exercise real, isolated POSIX process groups without a database or browser."""

# ruff: noqa: PT009, PT027 -- these also run on stock Python images without pytest.
from __future__ import annotations

import os
import signal
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from processes import _exited, _live_group, _signal_group, command, managed


class ProcessCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.env = {"PATH": os.defpath}
        self.python = [sys.executable, "-I", "-S", "-c"]

    def test_exited_child_is_reaped_after_its_empty_group_is_checked(self) -> None:
        with managed("quick", [*self.python, "pass"], self.root, self.env, self.root) as (proc, _):
            self.assertTrue(_exited(proc, 5))
            self.assertIsNone(proc.returncode)
            self.assertFalse(_live_group(proc.pid))
        self.assertEqual(proc.returncode, 0)

    def test_command_preserves_status_stderr_and_timeout(self) -> None:
        script = "import sys; print('fixture error', file=sys.stderr); sys.exit(47)"
        with self.assertRaisesRegex(RuntimeError, "script exited 47\nfixture error"):
            command("script", [*self.python, script], self.root, self.env, self.root, timeout=5)
        with self.assertRaisesRegex(RuntimeError, "slow exceeded 1s"):
            command(
                "slow",
                [*self.python, "import time; time.sleep(15)"],
                self.root,
                self.env,
                self.root,
                timeout=1,
            )

    def test_term_resistant_orphan_is_gone_when_context_exits(self) -> None:
        script = """
import os, signal, time
if os.fork():
    os._exit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print('ready', flush=True)
time.sleep(15)
"""
        with managed("fork", [*self.python, script], self.root, self.env, self.root) as (proc, log):
            self.assertTrue(_exited(proc, 5))
            deadline = time.monotonic() + 5
            while "ready" not in log.read_text():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertTrue(_live_group(proc.pid))
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(_live_group(proc.pid))

    def test_permission_error_on_a_live_group_remains_visible(self) -> None:
        argv = [*self.python, "import time; time.sleep(15)"]
        with (
            managed("live", argv, self.root, self.env, self.root) as (proc, _),
            patch("os.killpg", side_effect=PermissionError),
            self.assertRaises(PermissionError),
        ):
            _signal_group(proc, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
