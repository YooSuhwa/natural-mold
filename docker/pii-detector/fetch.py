# pyright: reportMissingImports=false
# Runs in the pii-detector image (torch, schift-ko-pii), not the backend venv.
"""One-shot: download the pinned checkpoint into PII_MODEL_DIR unless it is already there.

A failed download or a hash mismatch is reported and exits 0; pii-detector refuses any files
that do not match model_files.FILES, so nothing unpinned is ever loaded.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download
from model_files import FILES, REPO_ID, REVISION, mismatched

MODEL_DIR = Path(os.environ.get("PII_MODEL_DIR", "/models/schift-ko-pii-v7"))


def main() -> int:
    if not mismatched(MODEL_DIR):
        print("pii-model-fetch: reusing", MODEL_DIR)
        return 0
    try:
        for name in FILES:
            hf_hub_download(REPO_ID, name, revision=REVISION, local_dir=MODEL_DIR)
    except Exception as exc:  # noqa: BLE001 - any failure leaves the rules pack in use
        # Exit 0 so `compose up` finishes; pii-detector then answers 503 and the backend
        # uses its Korean rules pack (docs/docker-quickstart.md).
        print(f"pii-model-fetch: download failed ({type(exc).__name__}): {exc}", flush=True)
        return 0
    bad = mismatched(MODEL_DIR)
    if bad:
        print("pii-model-fetch: hash mismatch, not using:", ", ".join(bad), flush=True)
        return 0
    print("pii-model-fetch: downloaded", REPO_ID, "@", REVISION[:8], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
