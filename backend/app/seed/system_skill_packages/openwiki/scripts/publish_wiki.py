"""Publish the workspace wiki into the conversation outputs directory.

Copies ``<workspace>/openwiki/**/*.md`` into ``$OUTPUTS_DIR/openwiki/`` so the
generated pages surface as conversation artifacts, then stamps
``.last-update.json`` with the current git HEAD as the sync point for the
next update run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 200
GIT_TIMEOUT_SECONDS = 60


def _fail(message: str) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False))
    sys.exit(1)


def _outputs_dir() -> Path:
    raw = os.environ.get("OUTPUTS_DIR") or os.environ.get("SKILL_OUTPUT_DIR")
    if not raw:
        _fail("OUTPUTS_DIR is not set; this script must run through execute_in_skill")
    return Path(raw).resolve()


def _data_root(outputs_dir: Path) -> Path:
    if outputs_dir.parent.name != "conversations":
        _fail(f"unexpected OUTPUTS_DIR layout: {outputs_dir}")
    return outputs_dir.parent.parent


def _git_head(workspace: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607 — fixed args; git via PATH
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="workspace directory name")
    args = parser.parse_args()

    slug = args.workspace.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]+", slug) or slug.strip("-.") != slug:
        _fail(f"invalid workspace name: {args.workspace}")

    outputs_dir = _outputs_dir()
    workspace = (_data_root(outputs_dir) / "workspaces" / slug).resolve()
    wiki_dir = workspace / "openwiki"
    if not wiki_dir.is_dir():
        _fail(f"no wiki directory at /workspaces/{slug}/openwiki — write pages there first")

    pages = sorted(p for p in wiki_dir.rglob("*.md") if p.is_file())
    if not pages:
        _fail(f"no markdown pages found under /workspaces/{slug}/openwiki")
    if len(pages) > MAX_FILES:
        _fail(f"refusing to publish more than {MAX_FILES} pages ({len(pages)} found)")

    published: list[str] = []
    skipped: list[str] = []
    dest_root = outputs_dir / "openwiki"
    for page in pages:
        rel = page.relative_to(wiki_dir)
        if page.stat().st_size > MAX_FILE_BYTES:
            skipped.append(rel.as_posix())
            continue
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(page, dest)
        published.append((Path("openwiki") / rel).as_posix())

    head = _git_head(workspace)
    state = {
        "updatedAt": datetime.now(UTC).isoformat(),
        "gitHead": head,
        "command": "publish",
    }
    (wiki_dir / ".last-update.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "workspace": slug,
                "published": published,
                "skipped_over_size_limit": skipped,
                "count": len(published),
                "git_head": head,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
