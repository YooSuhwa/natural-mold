"""Verify recorded public responses with the staged official TypeScript SDK."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path
from uuid import UUID

CAPTURES = {
    "http-start-transcript.json": ("start", "success"),
    "http-recover-transcript.json": ("recover", "success"),
    **{
        f"ag-ui-approval-{kind}-{stage}.json": (f"{kind}-{stage}", outcome)
        for kind in ("single", "mixed", "rejected")
        for stage, outcome in (("interrupt", "interrupt"), ("continuation", "success"))
    },
}


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid SDK identifier")
    try:
        return str(UUID(value))
    except ValueError:
        # Protocol and model IDs are opaque. Keep equality across captures without exposing text.
        return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _receipt(file: Path, result: dict) -> dict:
    name, outcome = CAPTURES[file.name]
    capture = json.loads(file.read_bytes())
    expected = capture["expect"]
    frames = result["frames"]
    checked = result["requestChecked"]
    if (
        result["name"] != name
        or result["outcome"] != outcome
        or type(frames) is not int
        or frames < 1
        or frames != len(capture["events"])
        or type(checked) is not bool
        or checked != ("request" in capture)
    ):
        raise ValueError("Unexpected SDK capture identity or terminal")
    ids = {}
    for key in ("interruptIds", "messageIds", "toolCallIds"):
        if not isinstance(result[key], list):
            raise TypeError("Unexpected SDK identifier list")
        ids[key] = [_safe_id(value) for value in result[key]]

    errors = []
    if not checked and name != "recover":
        errors.append("required_request_not_checked")
    if bool(ids["interruptIds"]) != (outcome == "interrupt"):
        errors.append("unexpected_interrupt_ids")
    if name.endswith("-continuation") and checked and not capture["request"].get("resume"):
        errors.append("missing_resume_decision")
    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "sdk_stream_checked": True,
        "request_checked": checked,
        "outcome": outcome,
        "frames": frames,
        "thread_id": _safe_id(expected["threadId"]),
        "run_id": _safe_id(expected["runId"]),
        **ids,
    }


def typescript_receipts(root: Path, output: Path, env: dict[str, str], logs: Path) -> dict:
    """Write safe receipts for all available captures. A partial set never clears the gate."""
    available = [name for name in CAPTURES if (output / name).is_file()]
    missing = [name for name in CAPTURES if name not in available]
    captured = {}
    errors = []
    attempts = []
    node = shutil.which("node", path=env.get("PATH"))
    version = None
    if node:
        with suppress(OSError, subprocess.SubprocessError):
            version = subprocess.check_output(
                [node, "--version"],
                cwd=root,
                env=env,
                stderr=subprocess.DEVNULL,
                timeout=10,
                text=True,
            ).strip()
    if not re.fullmatch(r"v22\.\d+\.\d+", version or ""):
        version = None
        errors.append("node_22_required")

    def verify(executable: str, files: list[str]) -> list[dict] | None:
        try:
            result = subprocess.run(
                [
                    executable,
                    str(root / "scripts/ag_ui_acceptance/verify_transcripts.mjs"),
                    str(root / "frontend/e2e/helpers/ag-ui-sdk-verification.ts"),
                    *(str(output / name) for name in files),
                ],
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=90,
                check=True,
            )
            rows = json.loads(result.stdout)["captures"]
            if not isinstance(rows, list) or len(rows) != len(files):
                raise ValueError("Unexpected SDK result count")
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            attempts.append({"files": files, "ok": False})
            return None
        attempts.append({"files": files, "ok": True})
        return rows

    if node and version and available:
        # The verifier exits on the first invalid capture. Replay independently to retain all
        # successful checks and still record the batch failure.
        batch = verify(node, available)
        if batch is None:
            errors.append("sdk_batch_failed")
        for i, name in enumerate(available):
            row = None
            if batch is not None:
                row = batch[i]
            elif len(available) > 1:
                single = verify(node, [name])
                if single:
                    row = single[0]
            if row is None:
                captured[name] = {
                    "status": "failed",
                    "errors": ["sdk_verification_failed"],
                }
                continue
            try:
                captured[name] = _receipt(output / name, row)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                captured[name] = {"status": "failed", "errors": ["invalid_sdk_result"]}
    elif not version:
        captured = {
            name: {"status": "failed", "errors": ["node_22_required"]} for name in available
        }
    if missing:
        errors.append("missing_captures")
    if any(case["status"] != "passed" for case in captured.values()):
        errors.append("unverified_capture")
    receipt = {
        "status": "failed" if errors else "passed",
        "node": version,
        "requested": list(CAPTURES),
        "available": available,
        "missing": missing,
        "captures": captured,
        "errors": errors,
    }
    (logs / "typescript-sdk.log").write_text(json.dumps(attempts) + "\n")
    (output / "sdk-transcripts.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
