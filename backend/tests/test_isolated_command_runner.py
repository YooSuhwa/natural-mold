"""End-to-end tests for the hermetic command wrapper lifecycle."""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

type JSONValue = None | bool | int | str | list[JSONValue] | dict[str, JSONValue]

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "run-isolated-command.sh"
DUMMY_SECRET = "dummy-secret-must-not-leak"
BASH = "/bin/bash"


def _run(
    tmp_path: Path, cwd: str, command: list[str], *, environment: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, JSONValue]]:
    manifest = tmp_path / "cleanup.json"
    result = subprocess.run(
        [BASH, str(RUNNER), "--cwd", cwd, "--", *command],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "MOLDY_CLEANUP_MANIFEST": str(manifest),
            "DUMMY_INHERITED_SECRET": DUMMY_SECRET,
            **(environment or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, json.loads(manifest.read_text())


@pytest.mark.parametrize(("exit_code", "status"), [(0, "passed"), (17, "failed")])
def test_runner_cleans_root_and_propagates_exit(
    tmp_path: Path, exit_code: int, status: str
) -> None:
    # Given: a child that records its root and exits deterministically.
    receipt = tmp_path / "child-root.txt"
    code = (
        "import os,pathlib,sys; "
        f"pathlib.Path({str(receipt)!r}).write_text(os.environ['MOLDY_TEST_RUN_ROOT']); "
        "assert os.environ['MOLDY_DISABLE_ENV_FILE']=='true'; "
        "assert os.environ['PYTHON_DOTENV_DISABLED']=='1'; "
        f"sys.exit({exit_code})"
    )

    # When: the command runs through the wrapper.
    result, manifest = _run(tmp_path, "backend", [sys.executable, "-c", code])

    # Then: exit status is forwarded and only the owned root is removed.
    root = Path(receipt.read_text())
    assert result.returncode == exit_code
    assert root.parent.resolve() == Path(os.environ.get("TMPDIR", "/tmp")).resolve()
    assert root.name.startswith(".moldy-test-run.")
    assert not root.exists()
    assert manifest == {
        "schema_version": 1,
        "status": status,
        "child_exit_code": exit_code,
        "cleanup": "removed",
        "run_root_sha256": manifest["run_root_sha256"],
    }
    combined = result.stdout + result.stderr + json.dumps(manifest)
    assert DUMMY_SECRET not in combined
    assert str(root) not in combined


@pytest.mark.parametrize(("exit_code", "status"), [(0, "passed"), (17, "failed")])
def test_runner_writes_canonical_static_receipt_bytes(
    tmp_path: Path, exit_code: int, status: str
) -> None:
    # Given: a child with a deterministic passing or failing exit code.
    code = f"import sys; raise SystemExit({exit_code})"

    # When: the wrapper writes its manifest through the isolated writer.
    result, manifest = _run(tmp_path, "backend", [sys.executable, "-c", code])

    # Then: the raw receipt is compact, key-sorted, and newline-terminated.
    receipt = tmp_path / "cleanup.json"
    expected = (
        "{"
        f'"child_exit_code":{exit_code},'
        '"cleanup":"removed",'
        f'"run_root_sha256":"{manifest["run_root_sha256"]}",'
        '"schema_version":1,'
        f'"status":"{status}"'
        "}\n"
    ).encode()
    assert result.returncode == exit_code
    assert receipt.read_bytes() == expected


@pytest.mark.parametrize(("exit_code", "status"), [(0, "passed"), (17, "failed")])
def test_runner_writes_canonical_static_receipt_to_stdout(exit_code: int, status: str) -> None:
    # Given: a child run without a cleanup-manifest environment variable.
    environment = {
        key: value for key, value in os.environ.items() if key != "MOLDY_CLEANUP_MANIFEST"
    }
    code = f"import sys; raise SystemExit({exit_code})"

    # When: the wrapper falls back to its stdout receipt channel.
    result = subprocess.run(
        [BASH, str(RUNNER), "--cwd", "backend", "--", sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=False,
        check=False,
    )

    # Then: it writes one compact, key-sorted JSON line and no second newline.
    payload = json.loads(result.stdout)
    expected = (
        "{"
        f'"child_exit_code":{exit_code},'
        '"cleanup":"removed",'
        f'"run_root_sha256":"{payload["run_root_sha256"]}",'
        '"schema_version":1,'
        f'"status":"{status}"'
        "}\n"
    ).encode()
    assert result.returncode == exit_code
    assert result.stdout == expected


def test_runner_cleans_root_on_sigint(tmp_path: Path) -> None:
    # Given: a child that reports readiness through an inherited POSIX pipe.
    manifest = tmp_path / "cleanup.json"
    receipt = tmp_path / "child-root.txt"
    read_fd, write_fd = os.pipe()
    code = (
        "import os,pathlib,time; "
        f"pathlib.Path({str(receipt)!r}).write_text(os.environ['MOLDY_TEST_RUN_ROOT']); "
        f"os.write({write_fd}, b'ready'); "
        "time.sleep(30)"
    )
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [BASH, str(RUNNER), "--cwd", "backend", "--", sys.executable, "-c", code],
            cwd=REPO_ROOT,
            env={**os.environ, "MOLDY_CLEANUP_MANIFEST": str(manifest)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(write_fd,),
        )
        os.close(write_fd)
        write_fd = -1
        readable, _, _ = select.select([read_fd], [], [], 10)
        assert readable, "child did not report readiness through the inherited pipe"
        assert os.read(read_fd, 5) == b"ready"

        # When: the wrapper receives SIGINT after deterministic child readiness.
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)

        # Then: the signal status is forwarded and cleanup is durable.
        root = Path(receipt.read_text())
        assert process.returncode == 130
        assert not root.exists()
        payload = json.loads(manifest.read_text())
        assert payload["status"] == "interrupted"
        assert payload["cleanup"] == "removed"
        assert str(root) not in stdout + stderr + manifest.read_text()
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--cwd", "backend"],
        ["--cwd", "../backend", "--", "true"],
        ["--cwd", "other", "--", "true"],
    ],
)
def test_runner_rejects_invalid_cwd_or_argv(arguments: list[str]) -> None:
    # Given: malformed or path-escaping wrapper arguments.

    # When: the wrapper parses the boundary.
    result = subprocess.run(
        [BASH, str(RUNNER), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: it fails before running a child.
    assert result.returncode == 64


def test_runner_does_not_eval_child_arguments(tmp_path: Path) -> None:
    # Given: an argv item containing shell metacharacters.
    marker = tmp_path / "must-not-exist"
    malicious = f"value; touch {marker}"

    # When: the argument is passed to a direct-exec child.
    result, _ = _run(
        tmp_path,
        "frontend",
        [sys.executable, "-c", "import sys; assert sys.argv[1].startswith('value;')", malicious],
    )

    # Then: the child receives one literal argument and no injected command runs.
    assert result.returncode == 0
    assert not marker.exists()


def test_runner_ignores_stale_inherited_root(tmp_path: Path) -> None:
    # Given: an existing unrelated root inherited from another run.
    stale = tmp_path / "stale"
    stale.mkdir()
    marker = stale / "keep"
    marker.write_text("owned elsewhere")
    receipt = tmp_path / "child-root.txt"
    code = (
        "import os,pathlib; "
        f"pathlib.Path({str(receipt)!r}).write_text(os.environ['MOLDY_TEST_RUN_ROOT'])"
    )

    # When: a fresh wrapper run starts and completes.
    result, _ = _run(
        tmp_path,
        "backend",
        [sys.executable, "-c", code],
        environment={"MOLDY_TEST_RUN_ROOT": str(stale)},
    )

    # Then: it creates a different root and never removes the inherited path.
    assert result.returncode == 0
    assert Path(receipt.read_text()) != stale
    assert marker.read_text() == "owned elsewhere"


@pytest.mark.parametrize(
    "case",
    [
        ("directory", 0, 74),
        ("directory", 17, 17),
        ("symlink", 0, 74),
        ("file", 0, 74),
        ("absent", 0, 74),
    ],
)
def test_runner_never_deletes_a_replacement_after_owned_root_is_renamed(
    tmp_path: Path, case: tuple[str, int, int]
) -> None:
    # Given: a child renames the owned root and optionally installs a replacement.
    replacement_kind, child_exit, expected_exit = case
    receipt = tmp_path / "swap.json"
    external = tmp_path / "external"
    code = (
        "import json,os,pathlib; "
        "root=pathlib.Path(os.environ['MOLDY_TEST_RUN_ROOT']); "
        "renamed=root.with_name(root.name+'.renamed-probe'); root.rename(renamed); "
        f"kind={replacement_kind!r}; external=pathlib.Path({str(external)!r}); "
        "external.mkdir(exist_ok=True); (external/'keep').write_text('external'); "
        "root.mkdir() if kind=='directory' else None; "
        "(root/'replacement-marker').write_text('replacement') if kind=='directory' else None; "
        "(root/'nested-link').symlink_to(external, target_is_directory=True) "
        "if kind=='directory' else None; "
        "root.symlink_to(external, target_is_directory=True) if kind=='symlink' else None; "
        "root.write_text('replacement') if kind=='file' else None; "
        f"receipt=pathlib.Path({str(receipt)!r}); "
        "receipt.write_text(json.dumps({'root':str(root),'renamed':str(renamed)})); "
        f"raise SystemExit({child_exit})"
    )

    # When: the wrapper cleans up after the successful adversarial child.
    result, manifest = _run(tmp_path, "backend", [sys.executable, "-c", code])

    # Then: cleanup fails closed, preserving both the renamed owner and any replacement.
    paths = json.loads(receipt.read_text())
    root = Path(paths["root"])
    renamed = Path(paths["renamed"])
    try:
        assert result.returncode == expected_exit
        assert manifest["cleanup"] == "identity_mismatch"
        assert renamed.is_dir()
        if replacement_kind == "directory":
            assert (root / "replacement-marker").read_text() == "replacement"
            assert (root / "nested-link").is_symlink()
        elif replacement_kind == "symlink":
            assert root.is_symlink()
            assert (external / "keep").read_text() == "external"
        elif replacement_kind == "file":
            assert root.read_text() == "replacement"
        else:
            assert not root.exists()
    finally:
        if root.is_symlink() or root.is_file():
            root.unlink()
        elif root.is_dir():
            shutil.rmtree(root)
        if renamed.is_dir():
            shutil.rmtree(renamed)
