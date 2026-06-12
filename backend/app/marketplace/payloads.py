"""Helpers for immutable JSON marketplace payloads.

Skill marketplace versions are filesystem snapshots. Agent and MCP
marketplace versions are JSON snapshots, so they need a stable byte
representation and a recursive secret scan before publication.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.marketplace.secret_scan import SECRET_CONTENT_PATTERNS

_PLACEHOLDER_RE = re.compile(r"^\s*=?\s*(?:Bearer\s+)?\{\{\s*\$credentials\.[^}]+\}\}\s*$")

# Key names are matched on ``_``/``-`` separated *segments* so that
# ``DATABASE_PASS`` or ``X-Api-Key`` trip the scanner while substrings
# inside a segment (``passenger``, ``compass``, ``oauth``) and benign
# config keys (``LOG_LEVEL``, ``BASE_URL``, ``Content-Type``) do not.
_SECRET_KEY_RE = re.compile(
    r"(?:^|[-_])(?:"
    r"api[-_]?key|private[-_]?key|"
    r"pass(?:word)?|passwd|pwd|secrets?|token|credentials?|"
    r"auth|authorization|cookie"
    r")(?:$|[-_])",
    re.IGNORECASE,
)

# Marketplace schema fields whose name contains a secret-looking segment
# but whose value is a public identifier (a credential *definition* key
# such as ``mcp_oauth2``), never a secret value.
_SAFE_KEY_ALLOWLIST = frozenset({"credential_definition_key"})


@dataclass(frozen=True)
class PayloadSecretFinding:
    path: str
    kind: str
    pattern: str


def canonical_json_bytes(payload: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a marketplace payload."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_json_hash(payload: Any) -> str:
    """Return the SHA-256 hex digest of ``canonical_json_bytes(payload)``."""

    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value))


def _scan_string(*, path: str, key: str | None, value: str) -> list[PayloadSecretFinding]:
    if _is_placeholder(value):
        return []

    findings: list[PayloadSecretFinding] = []
    value_bytes = value.encode("utf-8", errors="ignore")
    for pattern in SECRET_CONTENT_PATTERNS:
        if pattern.search(value_bytes):
            findings.append(
                PayloadSecretFinding(
                    path=path,
                    kind="content",
                    pattern=pattern.pattern.decode("utf-8", errors="replace"),
                )
            )

    if (
        key is not None
        and value.strip()  # empty values carry no secret
        and key.strip().lower() not in _SAFE_KEY_ALLOWLIST
        and _SECRET_KEY_RE.search(key)
    ):
        findings.append(
            PayloadSecretFinding(
                path=path,
                kind="key",
                pattern=_SECRET_KEY_RE.pattern,
            )
        )
    return findings


def scan_payload(payload: Any) -> list[PayloadSecretFinding]:
    """Recursively scan a JSON-like payload for high-signal secrets.

    Credential interpolation placeholders such as
    ``={{ $credentials.access_token }}`` are allowed because they carry no
    secret value and are the expected marketplace contract.
    """

    findings: list[PayloadSecretFinding] = []

    def walk(value: Any, path: str, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                child_key_str = str(child_key)
                walk(child_value, f"{path}.{child_key_str}", child_key_str)
            return
        if isinstance(value, list):
            for index, child_value in enumerate(value):
                walk(child_value, f"{path}[{index}]", None)
            return
        if isinstance(value, str):
            findings.extend(_scan_string(path=path, key=key, value=value))

    walk(payload, "$")
    return findings


__all__ = [
    "PayloadSecretFinding",
    "canonical_json_bytes",
    "canonical_json_hash",
    "scan_payload",
]
