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

import math
import re
from collections import Counter
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
    # URL userinfo credentials — ``scheme://user:password@host``. The user
    # segment may be empty (``redis://:pass@host``). The password segment
    # excludes ``{`` so credential interpolation placeholders
    # (``https://u:{{$credentials.pw}}@host``) don't trip the scanner;
    # plain URLs / host:port never match (no ``@``).
    re.compile(rb"\b[A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]*:[^/\s@{]+@"),
    # Slack tokens (bot / user / app / refresh / legacy).
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # Telegram bot tokens — ``<bot_id>:<35-char secret>``.
    re.compile(rb"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),
    # Google OAuth access tokens.
    re.compile(rb"\bya29\.[A-Za-z0-9_\-]{10,}"),
    # GitHub server-to-server / OAuth / user-to-server tokens.
    re.compile(rb"\bgh[sou]_[A-Za-z0-9]{30,}\b"),
    # GitLab personal access tokens.
    re.compile(rb"\bglpat-[A-Za-z0-9_\-]{10,}\b"),
    # Token-bearing query strings — ``?access_token=…`` / ``&api_key=…``.
    # ``{`` is excluded from the value so interpolation placeholders
    # (``?token={{$credentials.x}}``) stay allowed. The leading ``[?&#]``
    # also catches OAuth implicit-flow fragments (``#access_token=…``).
    # Credential-bearing query keys are enumerated explicitly so a benign
    # ``?key=home-v2`` cache key isn't swept up unless it really names a
    # secret (``sig``/``signature``/``password``/``session``/``code`` …).
    re.compile(
        rb"[?&#](?:access_token|api_key|apikey|token|secret|key|"
        rb"sig|signature|password|pwd|session|sessionid|auth_code|code)"
        rb"=[^&\s{][^&\s]*",
        re.IGNORECASE,
    ),
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
# env_vars / headers literal-secret policy (allowlist-oriented)
# ---------------------------------------------------------------------------
#
# ``SECRET_CONTENT_PATTERNS`` is a *blocklist* — it catches known token
# shapes but misses opaque/custom secrets (an account-scoped bearer with
# no recognizable prefix). The publish gate for MCP ``env_vars`` /
# ``headers`` flips to an *allowlist*: a value is rejected unless it's
# clearly benign config. ``is_suspicious_secret_value`` below encodes that
# policy so the MCP payload builder and the agent-spec MCP dependency check
# stay in sync.

# Headers whose *name* means "this carries auth material". A literal
# (non-placeholder) value under any of these is always a secret.
#
# Earlier this matched bare ``key`` / ``token`` / ``auth`` segments, which
# false-positived on benign idempotency / cache / partition headers
# (``Idempotency-Key``, ``X-Cache-Key``, ``X-Request-Key`` …). The match
# is now an *enumerated allowlist of true credential-carrying header
# names* — a header only trips when its name is unambiguously about
# credentials, not merely because it ends in ``-key``.
_AUTH_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "api-key",
        "apikey",
        "x-api-key",
        "x-auth-token",
        "access-token",
        "auth-token",
        "refresh-token",
        "client-secret",
        "x-secret",
    }
)


def _is_auth_header_name(header_name: str) -> bool:
    return header_name.strip().lower() in _AUTH_HEADER_NAMES


# Separators that turn a value into a *structured identifier* rather than
# an opaque blob. A model name (``claude-3-5-sonnet-20241022``), a region
# (``prod-cluster-us-east-1a-replica``), a UUID, a reverse-DNS namespace
# (``com.example.service``), an npm spec (``@scope/pkg@latest``) and a
# ``Key: value`` pair all carry these. ``@`` is included because scoped
# package args / email-like identifiers use it but opaque base64/hex
# secrets never do (URL userinfo ``user:pass@host`` is caught earlier by
# the content patterns / URL branch).
_VALUE_SEPARATORS = frozenset("-_./:@ ")

# Minimum length for an unstructured (separator-free) value to even be
# considered an opaque secret. Below this every value passes.
_OPAQUE_VALUE_MIN_LEN = 20

# Shannon-entropy floor (bits/char) for a continuous run to read as
# random secret material rather than a low-entropy identifier.
_OPAQUE_ENTROPY_MIN = 3.0

# A pure-hex digest (md5/sha-style) has low per-char entropy but is still
# a classic secret/token shape, so a continuous hex run of this length is
# flagged even when its entropy dips below ``_OPAQUE_ENTROPY_MIN``. A small
# entropy floor still excludes a degenerate single-char repeat (``B*40``),
# which carries no real secret material and is an accepted residual miss.
_HEX_DIGEST_MIN_LEN = 32
_HEX_DIGEST_MIN_ENTROPY = 2.0

# A bare scheme://host(:port)/path URL is benign config even when long.
_URL_VALUE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://", re.IGNORECASE)

# A MIME / media type like ``application/json``. The subtype length is
# capped so an opaque secret smuggled as ``application/x-<secret>`` is not
# auto-allowed — values past the cap fall through to the opaque check.
_MIME_VALUE_RE = re.compile(
    r"^[A-Za-z0-9.\-]+/[A-Za-z0-9.\-+]{1,40}(?:\s*;.*)?$"
)

_HEX_RUN_RE = re.compile(r"^[0-9a-fA-F]+$")


def _looks_like_credential_placeholder(value: str) -> bool:
    # Local import keeps ``secret_scan`` import-light for the package walker
    # callers; ``publish_common`` already depends on ``secret_scan``-free
    # helpers so this avoids a cycle.
    return "{{" in value and "$credentials." in value


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )


def _matches_content_pattern(value: str) -> bool:
    value_bytes = value.encode("utf-8", errors="ignore")
    return any(pattern.search(value_bytes) for pattern in SECRET_CONTENT_PATTERNS)


def _rescan_url_value(value: str) -> bool:
    """Re-scan a URL's query string and fragment for embedded secrets.

    A bare ``scheme://host/path`` URL is benign config, but operators do
    paste signed URLs (``…?sig=…``) and OAuth implicit-flow redirects
    (``…#access_token=…``). Splitting on ``?`` / ``#`` and running the
    content patterns over the tail catches those without blocking the
    plain endpoint URL. (``SECRET_CONTENT_PATTERNS`` keys off ``[?&#]`` so
    the full value is scanned directly.)
    """

    return _matches_content_pattern(value)


def _is_opaque_secret_run(value: str) -> bool:
    """Heuristic for an unstructured high-entropy opaque secret.

    A value laced with separators (``-_./:`` or whitespace) reads as a
    structured identifier — model name, region/zone, UUID, reverse-DNS
    namespace, ``enum`` constant — and passes. Only a *continuous* run
    (no more than one separator) that is long and high-entropy, or a long
    pure-hex digest, is treated as opaque secret material.
    """

    separators = sum(1 for ch in value if ch in _VALUE_SEPARATORS)
    if separators >= 2:
        # Two or more delimiters → structured identifier, not a blob.
        return False
    if len(value) < _OPAQUE_VALUE_MIN_LEN:
        return False

    # Strip a single leading ``label=`` / ``label:`` so a ``session=<blob>``
    # cookie crumb is judged on its value, not the label noise.
    core = value
    for sep in ("=", ":"):
        if sep in core:
            core = core.split(sep, 1)[1]
            break
    core = core.strip()
    if len(core) < _OPAQUE_VALUE_MIN_LEN:
        # The opaque part alone is too short (rare ``a:bc`` config).
        return len(value) >= _OPAQUE_VALUE_MIN_LEN and _shannon_entropy(
            value
        ) >= _OPAQUE_ENTROPY_MIN

    if (
        _HEX_RUN_RE.match(core)
        and len(core) >= _HEX_DIGEST_MIN_LEN
        and _shannon_entropy(core) >= _HEX_DIGEST_MIN_ENTROPY
    ):
        return True
    return _shannon_entropy(core) >= _OPAQUE_ENTROPY_MIN


def is_suspicious_secret_value(value: object, *, header_name: str | None = None) -> bool:
    """Allowlist policy for an ``env_vars`` / ``headers`` / ``args`` value.

    Returns ``True`` when a literal value should block publication. The
    caller must instead use ``{{$credentials.x}}`` interpolation.

    Policy (allowlist-oriented — benign config must pass; we accept that
    free-text opaque secrets without a recognizable shape may slip through,
    a residual risk already documented for self-hosted config):

    * non-string / empty / whitespace-only → allowed (no secret)
    * credential placeholder → allowed (the expected contract)
    * value matched by a known ``SECRET_CONTENT_PATTERNS`` token → blocked
    * credential-carrying header name (``Authorization``, ``X-Api-Key`` …)
      *and* the value carries a content pattern or opaque secret run →
      blocked
    * URL → blocked only if its query/fragment carries a content pattern
    * MIME type (subtype <= 40 chars) → allowed
    * continuous high-entropy run (>= 20 chars, entropy >= 3.0) or long
      pure-hex digest → blocked
    * everything else (structured identifiers, short config, multi-word
      strings) → allowed
    """

    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if _looks_like_credential_placeholder(stripped):
        return False

    # Known token shapes (sk-, ghp_, Slack, query secrets, URL userinfo …)
    # are always blocked regardless of structure/length.
    if _matches_content_pattern(stripped):
        return True

    is_auth_header = header_name is not None and _is_auth_header_name(header_name)

    # URLs are benign endpoints unless their query/fragment hides a secret.
    if _URL_VALUE_RE.match(stripped):
        return _rescan_url_value(stripped)

    # MIME types with a sane subtype length are benign config — but a
    # secret smuggled as ``application/x-<blob>`` is cross-checked: the
    # subtype itself is run through the opaque-secret heuristic, so a
    # structured subtype (``x-www-form-urlencoded``) passes while a
    # continuous high-entropy one (``x-realsecrettoken12345``) is caught.
    if _MIME_VALUE_RE.match(stripped):
        subtype = stripped.split("/", 1)[1].split(";", 1)[0].strip()
        return _is_opaque_secret_run(subtype)

    opaque = _is_opaque_secret_run(stripped)

    # A credential-carrying header still requires the value to actually
    # look secret (content pattern handled above, or an opaque run). A
    # benign literal under ``Authorization`` (e.g. ``Negotiate``) is not a
    # leak; an opaque bearer is.
    if is_auth_header and opaque:
        return True

    return opaque


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
    "is_suspicious_secret_value",
    "scan_package",
]
