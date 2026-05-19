"""Marketplace secret scanner — Spec §13.1 / §6.

Pure-function module. Walks an extracted ``.skill`` package directory
and flags files whose **name** matches a known-leaky pattern (e.g.
``.env``, ``cookies.txt``) OR whose **content** contains a high-signal
secret (OpenAI keys, PEM private keys, AWS env-var names).

No DB, no I/O outside ``read_bytes`` — consumed identically by:

* ``publish_service.publish_skill_to_marketplace`` (Slice C)
* ``install_service.install_item`` (Slice B) when the install path
  unpacks a foreign package (k-skill import, Slice F)
* ``routers/skills.py:upload`` (regression gate — Spec §13.1)

Returns the full list of findings rather than short-circuiting on the
first hit so the operator sees the full scope in a single response.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


# Filename patterns are matched against the **basename**, case-insensitive.
# Use raw strings + explicit anchors so ``.env.local`` matches but
# ``hidden-environments.md`` doesn't.
SECRET_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"^secrets\.env$", re.IGNORECASE),
    re.compile(r".*\.pem$", re.IGNORECASE),
    re.compile(r".*\.key$", re.IGNORECASE),
    re.compile(r".*\.p12$", re.IGNORECASE),
    re.compile(r".*\.pfx$", re.IGNORECASE),
    re.compile(r"^cookies.*$", re.IGNORECASE),
    re.compile(r"^token.*$", re.IGNORECASE),
    re.compile(r".*credentials\.json$", re.IGNORECASE),
)


# Content patterns. **Word boundaries** on ``sk-`` (Bezos OI-4) so test
# fixtures / docstrings containing ``sk-example`` or ``sk-foo`` don't
# trip a false positive — only genuine ≥20-char OpenAI/Anthropic keys
# match.
SECRET_CONTENT_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    # OpenAI / Anthropic / Cohere etc.
    re.compile(rb"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    # PEM-encoded private keys (RSA, EC, DSA, OpenSSH, plain).
    re.compile(
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |)PRIVATE KEY-----"
    ),
    # AWS env exports — operators leak shell history into READMEs.
    re.compile(rb"AWS_SECRET_ACCESS_KEY\s*[:=]"),
    # Google ADC.
    re.compile(rb"GOOGLE_APPLICATION_CREDENTIALS\s*[:=]"),
    # GitHub fine-grained / classic.
    re.compile(rb"\bghp_[A-Za-z0-9]{30,}\b"),
    # Stripe live keys.
    re.compile(rb"\bsk_live_[A-Za-z0-9]{20,}\b"),
)


# Cap per-file scan size. A 10 MB .skill can still contain a useful
# scripts/ tree but reading multi-GB blobs through ``read_bytes`` would
# OOM the worker. ~256 KB covers normal config / SKILL.md / scripts.
_MAX_CONTENT_SCAN_BYTES = 256 * 1024


# Binary extensions where content scanning would only produce false
# negatives (compressed) or false positives (random ASCII inside
# images). Skip the content step for these; filename pattern still runs.
_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".mp3",
        ".mp4",
        ".mov",
        ".wav",
        ".so",
        ".dylib",
        ".bin",
    }
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretFinding:
    """One reason a file was flagged.

    * ``path``    — POSIX-style path **relative to** the package root.
    * ``kind``    — ``"filename"`` (matched ``SECRET_FILE_PATTERNS``) or
                    ``"content"`` (matched ``SECRET_CONTENT_PATTERNS``).
    * ``pattern`` — regex source string for the matching rule.
    """

    path: str
    kind: str  # 'filename' | 'content'
    pattern: str


# ---------------------------------------------------------------------------
# Scan entry points
# ---------------------------------------------------------------------------


def _iter_files(extracted_dir: Path) -> Iterable[Path]:
    """Yield every regular file under ``extracted_dir``. Skips
    directories + symlinks (the packager already strips symlinks; this
    is defense-in-depth so secret content reached via a stray symlink
    doesn't get walked)."""

    for entry in extracted_dir.rglob("*"):
        if entry.is_symlink():
            continue
        if not entry.is_file():
            continue
        yield entry


def _check_filename(rel_path: str) -> SecretFinding | None:
    basename = Path(rel_path).name
    for pattern in SECRET_FILE_PATTERNS:
        if pattern.match(basename):
            return SecretFinding(
                path=rel_path, kind="filename", pattern=pattern.pattern
            )
    return None


def _check_content(rel_path: str, file_path: Path) -> list[SecretFinding]:
    suffix = file_path.suffix.lower()
    if suffix in _BINARY_EXTENSIONS:
        return []
    try:
        # ``read_bytes`` then slice — keeps the file handle dance simple
        # under the cap. Small files (most config blobs) are <8 KB.
        head = file_path.read_bytes()[:_MAX_CONTENT_SCAN_BYTES]
    except OSError:
        return []
    findings: list[SecretFinding] = []
    for pattern in SECRET_CONTENT_PATTERNS:
        if pattern.search(head):
            findings.append(
                SecretFinding(
                    path=rel_path,
                    kind="content",
                    pattern=pattern.pattern.decode("utf-8", errors="replace"),
                )
            )
    return findings


def scan_package(extracted_dir: Path) -> list[SecretFinding]:
    """Walk the extracted directory and return every secret hit.

    The first hit per pattern per file is reported; further hits on the
    same pattern in the same file are deduped to keep the operator-
    facing list short. Different patterns matching the same file still
    yield separate findings (so the user knows *why* it's blocked).
    """

    root = extracted_dir.resolve()
    seen: set[tuple[str, str]] = set()  # (path, pattern)
    findings: list[SecretFinding] = []

    for entry in _iter_files(root):
        try:
            rel = entry.relative_to(root).as_posix()
        except ValueError:
            # Should not happen because ``rglob`` stays under root, but
            # tolerate to avoid a 500.
            continue

        name_hit = _check_filename(rel)
        if name_hit is not None and (rel, name_hit.pattern) not in seen:
            seen.add((rel, name_hit.pattern))
            findings.append(name_hit)

        for content_hit in _check_content(rel, entry):
            key = (rel, content_hit.pattern)
            if key in seen:
                continue
            seen.add(key)
            findings.append(content_hit)

    return findings


__all__ = [
    "SECRET_CONTENT_PATTERNS",
    "SECRET_FILE_PATTERNS",
    "SecretFinding",
    "scan_package",
]
