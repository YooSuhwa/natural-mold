"""Clone or update a git repository into the shared workspaces directory.

Prints a JSON summary (workspace paths, commit evidence, init/update mode)
that the agent uses to plan documentation work. Uses only the standard
library; git is invoked through subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CLONE_DEPTH_DEFAULT = 200
GIT_TIMEOUT_SECONDS = 300
LOG_TIMEOUT_SECONDS = 60
MAX_EVIDENCE_CHARS = 8000
FALLBACK_LOG_COMMITS = 20


def _fail(message: str) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False))
    sys.exit(1)


def _data_root() -> Path:
    """Derive the backend data root from OUTPUTS_DIR.

    The skill executor sets ``OUTPUTS_DIR=<data_root>/conversations/<thread_id>``,
    so the data root is two levels up. Fail loudly if the layout ever changes.
    """

    raw = os.environ.get("OUTPUTS_DIR") or os.environ.get("SKILL_OUTPUT_DIR")
    if not raw:
        _fail("OUTPUTS_DIR is not set; this script must run through execute_in_skill")
    out = Path(raw).resolve()
    if out.parent.name != "conversations":
        _fail(f"unexpected OUTPUTS_DIR layout: {out}")
    return out.parent.parent


def _slug_from_url(repo_url: str) -> str:
    tail = repo_url.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    return _sanitize_slug(tail)


def _sanitize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower()).strip("-.")
    if not slug:
        _fail("could not derive a workspace name; pass --workspace explicitly")
    return slug


def _git(args: list[str], *, cwd: Path | None, timeout: int) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {detail[:500]}")
    return result.stdout


def _commit_exists(workspace: Path, sha: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
        return False
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=str(workspace),
        capture_output=True,
        timeout=LOG_TIMEOUT_SECONDS,
        check=False,
    )
    return probe.returncode == 0


def _previous_head(workspace: Path) -> str | None:
    state_path = workspace / "openwiki" / ".last-update.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    head = state.get("gitHead")
    return head if isinstance(head, str) and head else None


def _sync(workspace: Path, repo_url: str, ref: str | None, depth: int) -> None:
    if (workspace / ".git").is_dir():
        fetch = ["fetch", f"--depth={depth}", "origin"]
        fetch.append(ref if ref else "HEAD")
        _git(fetch, cwd=workspace, timeout=GIT_TIMEOUT_SECONDS)
        # reset --hard keeps untracked files, so the openwiki/ directory
        # (untracked in the target repo) survives the refresh.
        _git(["reset", "--hard", "FETCH_HEAD"], cwd=workspace, timeout=LOG_TIMEOUT_SECONDS)
        return
    workspace.parent.mkdir(parents=True, exist_ok=True)
    clone = ["clone", f"--depth={depth}"]
    if ref:
        clone += ["--branch", ref]
    clone += [repo_url, str(workspace)]
    _git(clone, cwd=None, timeout=GIT_TIMEOUT_SECONDS)


def _evidence(workspace: Path, previous_head: str | None) -> tuple[str, str]:
    head = _git(["rev-parse", "HEAD"], cwd=workspace, timeout=LOG_TIMEOUT_SECONDS).strip()
    if previous_head and previous_head != head and _commit_exists(workspace, previous_head):
        log = _git(
            ["log", "--oneline", "--name-status", f"{previous_head}..HEAD"],
            cwd=workspace,
            timeout=LOG_TIMEOUT_SECONDS,
        )
    else:
        log = _git(
            ["log", "--oneline", "--name-status", f"-{FALLBACK_LOG_COMMITS}"],
            cwd=workspace,
            timeout=LOG_TIMEOUT_SECONDS,
        )
    if len(log) > MAX_EVIDENCE_CHARS:
        log = log[:MAX_EVIDENCE_CHARS] + "\n... (truncated)"
    return head, log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", required=True, help="git repository URL to document")
    parser.add_argument("--ref", default=None, help="branch or tag to sync (default: remote HEAD)")
    parser.add_argument("--workspace", default=None, help="workspace directory name override")
    parser.add_argument("--depth", type=int, default=CLONE_DEPTH_DEFAULT)
    args = parser.parse_args()

    data_root = _data_root()
    slug = _sanitize_slug(args.workspace) if args.workspace else _slug_from_url(args.repo_url)
    workspace = (data_root / "workspaces" / slug).resolve()
    if workspace.parent != (data_root / "workspaces").resolve():
        _fail(f"workspace name escapes the workspaces directory: {slug}")

    previous_head = _previous_head(workspace)
    try:
        _sync(workspace, args.repo_url, args.ref, max(args.depth, 1))
        head, commits = _evidence(workspace, previous_head)
        files_total = len(
            _git(["ls-files"], cwd=workspace, timeout=LOG_TIMEOUT_SECONDS).splitlines()
        )
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        _fail(str(exc))
        return  # unreachable; keeps type-checkers happy

    wiki_dir = workspace / "openwiki"
    has_wiki = wiki_dir.is_dir() and any(wiki_dir.rglob("*.md"))
    top_level = sorted(
        p.name + ("/" if p.is_dir() else "") for p in workspace.iterdir() if p.name not in {".git"}
    )

    print(
        json.dumps(
            {
                "workspace": slug,
                "workspace_virtual_path": f"/workspaces/{slug}",
                "wiki_virtual_path": f"/workspaces/{slug}/openwiki",
                "mode": "update" if has_wiki else "init",
                "head": head,
                "previous_head": previous_head,
                "files_total": files_total,
                "top_level": top_level,
                "commits": commits,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
