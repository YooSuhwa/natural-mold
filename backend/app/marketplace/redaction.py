"""Marketplace redaction — Spec §8.5, §13.2.

Two complementary helpers:

* :func:`redact_credential_values` — Given a text blob (subprocess
  stdout, exception message, log line) and the mapped env vars that
  were injected for the current skill run, replace every occurrence of
  a credential value with ``<redacted:<env_var_name>>``. Operators can
  still cross-reference which credential a redaction came from without
  exposing the value.

* :func:`redact_keys` — Walk a JSON-ish payload (dict / list / scalar)
  and replace values under sensitive *key names* (password / api_key /
  secret / token / access_key / refresh_token) with the literal string
  ``"<redacted>"``. Heuristic — used at the streaming layer where the
  exact set of injected credentials isn't in scope.

Both functions are pure — no DB, no logging, no exceptions outside the
narrow ``TypeError`` raised on truly unexpected types. They run on the
hot path (per chunk / per tool result) and must stay allocation-light.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Field names whose values should never leak. Case-insensitive. Order
# doesn't matter — the compiled alternation matches any substring.
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|api[_-]?key|secret|token|access[_-]?key|refresh[_-]?token|"
    r"client[_-]?secret|private[_-]?key)",
    re.IGNORECASE,
)

# Values shorter than this are considered low signal — partial matches
# on e.g. ``id="x"`` or ``count=0`` would scrub user-visible output for
# no benefit. ``len(value) > 4`` lifted from Sat's Stage 4 brief.
_MIN_REDACT_LEN = 5

_REDACTED_KEY_PLACEHOLDER = "<redacted>"


def is_sensitive_key(name: str) -> bool:
    """Return ``True`` when a dict key matches the sensitive pattern."""

    return bool(_SENSITIVE_KEY_PATTERN.search(name))


def replace_secret_values(
    text: str,
    secret_values: Iterable[str] | None,
    *,
    placeholder: str,
) -> str:
    """Exact-substring replace every secret value in ``text``.

    Shared core (ADR-021): callers supply an iterable of plaintext secrets
    and the placeholder to substitute. Values shorter than
    :data:`_MIN_REDACT_LEN`, duplicates and non-``str`` entries are dropped.
    Values are sorted by length descending so a longer secret that contains a
    shorter one is replaced first (otherwise the shorter match would consume a
    fragment of the longer one and leave a dangling marker).

    Pure single-pass ``str.replace`` per value — no regex, so ReDoS is
    structurally impossible.
    """

    if not text or not secret_values:
        return text

    unique = {
        value for value in secret_values if isinstance(value, str) and len(value) >= _MIN_REDACT_LEN
    }
    out = text
    for value in sorted(unique, key=len, reverse=True):
        if value in out:
            out = out.replace(value, placeholder)
    return out


def redact_credential_values(text: str, mapped_env_vars: dict[str, str] | None) -> str:
    """Replace every credential value occurrence in ``text``.

    ``mapped_env_vars`` is ``{env_var_name: value}`` — the same dict
    composed in ``_create_skill_execute_tool`` and passed to the
    subprocess. Each non-empty value with at least
    :data:`_MIN_REDACT_LEN` chars gets replaced with
    ``<redacted:<env_var_name>>``.

    Sort values by length descending so a longer secret that contains
    a shorter one (rare but possible — e.g. base64-encoded shared
    prefix) is replaced first. Otherwise the shorter match would
    consume a fragment of the longer one and the redaction marker
    would be left dangling.
    """

    if not text or not mapped_env_vars:
        return text

    pairs: Iterable[tuple[str, str]] = sorted(
        (
            (env_name, value)
            for env_name, value in mapped_env_vars.items()
            if isinstance(value, str) and len(value) >= _MIN_REDACT_LEN
        ),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    out = text
    for env_name, value in pairs:
        if value in out:
            out = out.replace(value, f"<redacted:{env_name}>")
    return out


def redact_keys(payload: Any) -> Any:
    """Walk a JSON-shaped value and mask sensitive keys in dicts.

    Recurses into dict values and list items. Scalars pass through
    unchanged. Returns a new structure so callers can safely pass the
    result to ``json.dumps`` / FastAPI response models without
    accidentally re-using a mutated input downstream.

    Does NOT inspect string contents for credential values — that's
    the job of :func:`redact_credential_values` (which has the actual
    secrets in scope). This function's contract is purely structural.
    """

    if isinstance(payload, dict):
        return {
            key: (
                _REDACTED_KEY_PLACEHOLDER
                if isinstance(key, str) and is_sensitive_key(key)
                else redact_keys(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_keys(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_keys(item) for item in payload)
    return payload


__all__ = [
    "is_sensitive_key",
    "redact_credential_values",
    "redact_keys",
    "replace_secret_values",
]
