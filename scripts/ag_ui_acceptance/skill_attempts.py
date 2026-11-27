"""Count actual DOCX process starts in the local two-worker acceptance stack."""

import os
import shlex
from pathlib import Path
from uuid import UUID


def docx_attempt_environment(
    directory: Path, node_binary: str | Path
) -> dict[str, str]:
    """Install a pass-through Node entry point shared by both throwaway workers."""
    node = Path(node_binary).resolve(strict=True)
    ledger = (directory / "docx-attempts.txt").resolve()
    wrapper = (directory / "docx-node").resolve()
    ledger.touch(mode=0o600, exist_ok=False)
    wrapper.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'case "${1:-}" in\n'
        "  scripts/create_docx.cjs|*/scripts/create_docx.cjs)\n"
        "    printf '%s\\n' \"${SKILL_OUTPUT_DIR##*/}\" >> "
        + shlex.quote(str(ledger))
        + ";;\nesac\n"
        + f'exec {shlex.quote(str(node))} "$@"\n'
    )
    wrapper.chmod(0o700)
    return {"SKILL_NODE_BINARY": str(wrapper), "MOLDY_AGUI_DOCX_ATTEMPTS": str(ledger)}


def docx_attempts(thread_id: str) -> int:
    # App subprocesses see a clean environment. Only the wrapper embeds the ledger path.
    ledger = Path(os.environ["MOLDY_AGUI_DOCX_ATTEMPTS"])
    return ledger.read_text().splitlines().count(str(UUID(thread_id)))
