"""The reviewed schift-ko-pii-v7 checkpoint: Hugging Face revision and file hashes.

modeling_lfm2_bidirectional.py runs as trust_remote_code; it is byte-identical to the v6 file
reviewed for docs/design-docs/korean-pii-detector-benchmark.md. Change a hash only after
reviewing the new file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ID = "schift-io/schift-ko-pii-v7"
REVISION = "9d9bc145c57371fd5fd70575b47f37af47a39728"
FILES = {
    "config.json": "81b782448560e6be20159b370c38cf2fb38c0d712370ba3c610daa201bfc1354",
    "model.safetensors": "b1b9a6f82e22cbaeebba882f1f8236161c0af8e3ae5baac07511955c32679f81",
    "modeling_lfm2_bidirectional.py": (
        "f171f518be2a07da48b17fdea5655cad0a2452ab548e90e8ae903143686647e2"
    ),
    "schift_heads.json": "4a9fc27133e55ea7546d9e4c35606ae3a5ed7967ba11daebd07a425d83197f4d",
    "tokenizer.json": "200c2fd5a65daf3b67aa532740d3a26736deceaa8b28e82826038aede9377cf0",
    "tokenizer_config.json": "f9051b19e43e2a1be479bc0ed9e27e1e40a43a2a32f4bd6d585b3e7045f62654",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def mismatched(model_dir: Path) -> list[str]:
    """Pinned files that are missing from model_dir or have another hash."""
    return [
        name
        for name, expected in FILES.items()
        if not (model_dir / name).is_file() or _sha256(model_dir / name) != expected
    ]
