"""Behavioral contract for the prepared backend/frontend lane tree."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "run-isolated-command.sh"


def test_backend_run_has_prepared_lane_tree_under_system_temp(tmp_path: Path) -> None:
    # Given: a child that observes only documented lane directories.
    receipt = tmp_path / "prepared.json"
    code = (
        "import json,os,pathlib; root=pathlib.Path(os.environ['MOLDY_TEST_RUN_ROOT']); "
        "names=['backend/data','frontend/auth','frontend/.next',"
        "'frontend/.next/scripted-smoke','frontend/.next/scripted-full',"
        "'frontend/.next/scripted-capture','frontend/.next/live-manual',"
        "'frontend/test-results','frontend/playwright-artifacts/scripted-smoke',"
        "'frontend/playwright-artifacts/scripted-full',"
        "'frontend/playwright-artifacts/live-manual','output/e2e-captures']; "
        f"pathlib.Path({str(receipt)!r}).write_text(json.dumps("
        "{'root':str(root),'ready':all((root/name).is_dir() for name in names),"
        "'legacy_next':(root/'frontend/next').exists()}))"
    )

    # When: the wrapper prepares and runs the backend child.
    result = subprocess.run(
        ["/bin/bash", str(RUNNER), "--cwd", "backend", "--", sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={**os.environ, "MOLDY_CLEANUP_MANIFEST": str(tmp_path / "manifest.json")},
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: preparation is complete outside the checkout and cleanup removes it.
    payload = json.loads(receipt.read_text())
    root = Path(payload["root"])
    assert result.returncode == 0, result.stderr
    assert payload["ready"] is True
    assert payload["legacy_next"] is False
    assert not root.is_relative_to(REPO_ROOT)
    assert not root.exists()


def test_frontend_child_runs_from_disposable_source_mirror(tmp_path: Path) -> None:
    # Given: a frontend child that records cwd and representative source/dependency boundaries.
    receipt = tmp_path / "frontend.json"
    code = (
        "import json,os,pathlib; cwd=pathlib.Path.cwd(); "
        "root=pathlib.Path(os.environ['MOLDY_TEST_RUN_ROOT']); "
        f"pathlib.Path({str(receipt)!r}).write_text(json.dumps("
        "{'cwd':str(cwd),'root':str(root),'package':(cwd/'package.json').is_file(),"
        "'source':(cwd/'src').is_dir(),'modules':(cwd/'node_modules').is_symlink()}))"
    )

    # When: the wrapper starts a frontend command.
    result = subprocess.run(
        ["/bin/bash", str(RUNNER), "--cwd", "frontend", "--", sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={**os.environ, "MOLDY_CLEANUP_MANIFEST": str(tmp_path / "manifest.json")},
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: cwd is the prepared mirror with copied source and read-through dependencies.
    payload = json.loads(receipt.read_text())
    assert result.returncode == 0, result.stderr
    assert Path(payload["cwd"]).resolve() == (Path(payload["root"]) / "frontend").resolve()
    assert payload["package"] is True
    assert payload["source"] is True
    assert payload["modules"] is True


def test_preparation_ignores_stale_playwright_artifacts_from_source_tree(tmp_path: Path) -> None:
    # Given: a source frontend containing stale Playwright output and a wrapper-owned run root.
    source_root = tmp_path / "source"
    source_frontend = source_root / "frontend"
    source_frontend.mkdir(parents=True)
    (source_frontend / "package.json").write_text("{}")
    (source_frontend / "node_modules").mkdir()
    stale_output = source_frontend / "playwright-artifacts/scripted-smoke/stale.txt"
    stale_output.parent.mkdir(parents=True)
    stale_output.write_text("untrusted")
    run_root = tmp_path / ".moldy-test-run.artifact-isolation"
    run_root.mkdir()

    # When: the isolated-run preparer copies the source tree.
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/prepare-isolated-run.py"),
            str(run_root),
            str(source_root),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the prepared empty artifact directory survives without mirroring stale source output.
    prepared_output = run_root / "frontend/playwright-artifacts/scripted-smoke"
    assert result.returncode == 0, result.stderr
    assert prepared_output.is_dir()
    assert not (prepared_output / stale_output.name).exists()


def test_real_next_type_setup_writes_only_inside_frontend_mirror(tmp_path: Path) -> None:
    # Given: source metadata digests before invoking the installed Next boundary.
    frontend = REPO_ROOT / "frontend"
    watched = [frontend / "tsconfig.json", frontend / "next-env.d.ts"]
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        for path in watched
    }
    receipt = tmp_path / "next-setup.json"
    node_code = (
        "const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');"
        "const cwd=process.cwd(),root=fs.realpathSync(process.env.MOLDY_TEST_RUN_ROOT);"
        "if(cwd!==path.join(root,'frontend'))process.exit(42);"
        "const run=cp.spawnSync('pnpm',['exec','next','typegen'],{cwd,stdio:'inherit'});"
        f"fs.writeFileSync({str(receipt)!r},JSON.stringify({{cwd,code:run.status,tsconfig:fs.existsSync(path.join(cwd,'tsconfig.json'))}}));"
        "process.exit(run.status??70)"
    )

    # When: the wrapper runs real Next TypeScript setup for the disposable mirror.
    result = subprocess.run(
        ["/bin/bash", str(RUNNER), "--cwd", "frontend", "--", "node", "-e", node_code],
        cwd=REPO_ROOT,
        env={**os.environ, "MOLDY_CLEANUP_MANIFEST": str(tmp_path / "manifest.json")},
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: setup succeeds in the mirror, source metadata is unchanged, and root is cleaned.
    assert result.returncode == 0, result.stderr
    payload = json.loads(receipt.read_text())
    assert payload["code"] == 0
    assert payload["tsconfig"] is True
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        for path in watched
    } == before
    assert not Path(payload["cwd"]).parent.exists()
