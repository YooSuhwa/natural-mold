from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.agent_runtime.memory_event_projection import MEMORY_EVENT_NAMES, MEMORY_TOOL_NAMES

REDACTED_MEMORY_FIELD: Final = "<redacted>"
REDACTED_SENSITIVE_FIELD: Final = "<redacted>"

# Usage / token-accounting keys are debug-critical and never carry secrets.
# Exact members are always safe; the predicate below also treats any
# ``*_tokens`` / ``token_count`` / ``*_token_details`` key as a metric.
SAFE_TOKEN_METRIC_KEYS: Final = frozenset(
    {
        "cache_creation_tokens",
        "cache_read_tokens",
        "completion_tokens",
        "estimated_cost",
        "input_token_details",
        "input_tokens",
        "output_token_details",
        "output_tokens",
        "prompt_tokens",
        "total_tokens",
        "usage",
        "usage_metadata",
    }
)

# Keys matched as whole tokens (exact only), never as substrings of unrelated
# words. ``cookies_enabled`` must NOT match ``cookie``; ``session_id`` /
# ``session_count`` must NOT match a bare ``session``.
_SENSITIVE_EXACT_KEYS: Final = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "proxy_authorization",
        "cookie",
        "set-cookie",
        "set_cookie",
        "session",
        "csrf",
        "xsrf",
    }
)

# Sensitive key fragments matched on word boundaries: a fragment must sit at
# the start/end of the key or be delimited by ``_`` / ``-``. This redacts
# ``api_key`` / ``access_token`` / ``session_token`` / ``csrf_token`` /
# ``refresh_token`` / ``client_secret`` / ``password`` while leaving
# ``session_id`` / ``session_count`` / ``token_count`` / ``tokens_used`` /
# ``possession`` / ``my_secretary`` / ``cookies_enabled`` untouched.
# ``session_token`` is covered by the ``token`` fragment (``_token`` suffix).
_SENSITIVE_KEY_FRAGMENT: Final = (
    r"password|passwd|"
    r"api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key|"
    r"secret|"
    r"token|"
    r"access[_-]?token|refresh[_-]?token|bearer[_-]?token|"
    r"client[_-]?secret|"
    r"csrf[_-]?token|xsrf[_-]?token"
)
SENSITIVE_PROTOCOL_KEY_RE: Final = re.compile(
    rf"(?:^|[_\-])(?:{_SENSITIVE_KEY_FRAGMENT})(?:[_\-]|$)",
    re.IGNORECASE,
)

# Token-metric keys to keep visible: exact members above plus any key ending
# in ``_tokens`` (e.g. ``reasoning_tokens``), the literal ``token_count``, or
# any ``*_token_details`` aggregate.
_SAFE_METRIC_KEY_RE: Final = re.compile(
    r"(?:^|[_\-])tokens$|^token_count$|_token_details$",
    re.IGNORECASE,
)

# Assignment leak: ``<key> = value`` / ``: value`` style fragments inside
# opaque strings (e.g. ``api_key=sk-...``). The key is matched as a *bounded*
# identifier (``{1,64}``) and re-validated by :func:`_is_sensitive_protocol_key`,
# so only sensitive keys are masked and non-sensitive assignments survive.
#
# ReDoS note: the previous pattern wrapped the key fragment in overlapping
# ``[A-Za-z0-9_-]*`` quantifiers (``…*(?:frag)…*``) which caused catastrophic
# backtracking on long runs of identifier characters (O(n^2)+). A single
# bounded ``{1,64}`` identifier class has no overlapping quantifier and caps
# backtracking, so attacker-controlled trace blobs can no longer stall the
# event loop on the persistence/streaming hot paths.
SENSITIVE_ASSIGNMENT_RE: Final = re.compile(
    r"([A-Za-z0-9_-]{1,64}[\"']?\s*[:=]\s*[\"']?(?:Bearer\s+)?)([^\"',}\]\s]+)([\"']?)",
    re.IGNORECASE,
)
ASSIGNMENT_KEY_RE: Final = re.compile(r"([A-Za-z0-9_-]+)")

# Value-based masking — secondary defence for secrets that appear with no
# (or an unrecognised) key in front of them. Each pattern keeps a harmless
# prefix and masks only the credential body so normal prose is preserved.
_VALUE_MASK_PATTERNS: Final = (
    # Bearer <token>  (also covers ``Authorization: Bearer ...`` output)
    (re.compile(r"\bBearer\s+\S+", re.IGNORECASE), "Bearer <redacted>"),
    # JWT — three base64url segments separated by dots, starting ``eyJ``.
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), "<redacted>"),
    # OpenAI-style keys: ``sk-...`` (incl. ``sk-proj-...``) with a long body.
    (re.compile(r"\bsk-(?:[A-Za-z0-9_-]+-)?[A-Za-z0-9]{20,}"), "<redacted>"),
    # Non-standard auth cookie token names emitted as ``moldy_at=...``.
    (re.compile(r"\b(moldy_(?:at|rt|csrf))=\S+", re.IGNORECASE), r"\1=<redacted>"),
    # URL/DSN userinfo credentials: ``scheme://user:pw@host`` -> mask ``user:pw``.
    # Any scheme (http(s), postgres, redis, mongodb, amqp, ...) so DSNs embedded
    # in trace/error strings don't leak their password.
    #
    # ReDoS note: the scheme is anchored with ``\b`` and length-bounded
    # (``{0,31}``). An unbounded ``[a-z0-9+.\-]*://`` scheme would start a match
    # at nearly every position of a long identifier run and scan forward for
    # ``://``, giving O(n^2) on attacker-controlled blobs (a plain 96KB token
    # run stalled ~23s). The bound + word-boundary removes those start sites.
    (
        re.compile(r"\b([a-z][a-z0-9+.\-]{0,31}://)[^/\s:@]+:[^/\s@]+(@)", re.IGNORECASE),
        r"\1<redacted>\2",
    ),
)


def redact_protocol_data(method: str, data: Any, *, redact_memory: bool = True) -> Any:
    redacted = _redact_sensitive_keys(data)
    if redact_memory and method == "tools":
        return _redact_tool_event(redacted)
    if redact_memory and method == "custom":
        return _redact_custom_event(redacted)
    return redacted


def _redact_sensitive_keys(data: Any) -> Any:
    if isinstance(data, Mapping):
        return {
            str(key): (
                REDACTED_SENSITIVE_FIELD
                if _is_sensitive_protocol_key(str(key))
                else _redact_sensitive_keys(value)
            )
            for key, value in data.items()
        }
    if isinstance(data, Sequence) and not isinstance(data, str | bytes | bytearray):
        return [_redact_sensitive_keys(item) for item in data]
    if isinstance(data, str):
        return _redact_sensitive_string(data)
    return data


def _normalize_key(key: str) -> str:
    # Split camelCase / digit->upper boundaries with ``_`` before lower-casing so
    # keys like ``sessionToken`` / ``XAuthToken`` / ``xApiKey`` still match the
    # word-boundary sensitive patterns. Without this, lower-casing erases the
    # boundary (``sessiontoken`` no longer has a ``_token`` segment) and the
    # secret key would leak — a regression vs. the old substring matcher.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    # Also split acronym->word boundaries so a sensitive fragment behind an
    # uppercase run isn't swallowed: ``IDToken`` -> ``ID_Token``,
    # ``JWTSecret`` -> ``JWT_Secret``, ``SSOToken`` -> ``SSO_Token``. Without
    # this the ``_token``/``_secret`` segment vanishes and the key leaks.
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", spaced)
    return spaced.lower()


def _is_sensitive_protocol_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in SAFE_TOKEN_METRIC_KEYS or _SAFE_METRIC_KEY_RE.search(normalized):
        return False
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    return SENSITIVE_PROTOCOL_KEY_RE.search(normalized) is not None


def _redact_sensitive_string(data: str) -> str:
    parsed = _parse_json_container(data)
    if parsed is not None:
        redacted = _redact_sensitive_keys(parsed)
        if redacted != parsed:
            return json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
    masked = SENSITIVE_ASSIGNMENT_RE.sub(_redact_assignment_match, data)
    return _mask_sensitive_values(masked)


def _mask_sensitive_values(data: str) -> str:
    for pattern, replacement in _VALUE_MASK_PATTERNS:
        data = pattern.sub(replacement, data)
    return data


def _parse_json_container(data: str) -> Any | None:
    stripped = data.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        parsed = json.loads(data)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping | Sequence) else None


def _redact_assignment_match(match: re.Match[str]) -> str:
    prefix = match.group(1)
    key_match = ASSIGNMENT_KEY_RE.match(prefix)
    if key_match and not _is_sensitive_protocol_key(key_match.group(1)):
        return match.group(0)
    return f"{prefix}{REDACTED_SENSITIVE_FIELD}{match.group(3)}"


def _redact_tool_event(data: Any) -> Any:
    if not isinstance(data, Mapping):
        return data
    tool_name = _text(data.get("name") or data.get("tool_name") or data.get("tool"))
    if tool_name not in MEMORY_TOOL_NAMES:
        return data

    safe = dict(data)
    for args_key in ("args", "parameters"):
        args = safe.get(args_key)
        if isinstance(args, Mapping):
            safe[args_key] = _redact_memory_mapping(args)
    return safe


def _redact_custom_event(data: Any) -> Any:
    if not isinstance(data, Mapping):
        return data
    name = _text(data.get("name") or data.get("channel"))
    payload = data.get("payload")
    if name not in MEMORY_EVENT_NAMES or not isinstance(payload, Mapping):
        return data
    return {**dict(data), "payload": _redact_memory_mapping(payload)}


def _redact_memory_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(data)
    if "content" in safe:
        safe["content"] = REDACTED_MEMORY_FIELD
    if safe.get("reason") is not None:
        safe["reason"] = REDACTED_MEMORY_FIELD
    return safe


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""
