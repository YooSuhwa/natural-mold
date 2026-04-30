"""Limited interpolation engine for ``={{ $credentials.<field> }}`` only.

This module deliberately does NOT evaluate JavaScript or arbitrary Python.
The grammar is fixed to a single expression form so the attack surface is
minimal: ``={{ $credentials.<identifier> }}`` (whitespace tolerant).

Integration: :func:`resolve` operates on a single string;
:func:`resolve_deep` walks dicts and lists for HTTP request specs.
"""

from __future__ import annotations

import re
from typing import Any

# ``={{ $credentials.<name> }}`` — leading ``=`` is optional (expression marker
# borrowed from prior art, see NOTICES.md). The field name is restricted to
# identifier characters to keep parsing unambiguous and to forbid path
# traversal / function calls.
_PATTERN = re.compile(
    r"=?\{\{\s*\$credentials\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
)


class InterpolationError(ValueError):
    """Raised when a referenced credential field is missing."""


def resolve(template: Any, credentials: dict[str, Any]) -> Any:
    """Resolve ``={{ $credentials.X }}`` placeholders inside ``template``.

    Non-string values are returned as-is. A leading ``=`` marks the entire
    string as an expression and is stripped from the output so that
    ``"=Bearer {{ $credentials.api_key }}"`` becomes ``"Bearer <value>"``.

    If the template is exactly one placeholder (e.g.
    ``"={{ $credentials.api_key }}"``), the original credential value is
    returned without coercing it to string — important for dict/number fields.
    """

    if not isinstance(template, str):
        return template

    # Fast path: is the entire string a single placeholder?
    full_match = _PATTERN.fullmatch(template)
    if full_match:
        name = full_match.group("name")
        if name not in credentials:
            raise InterpolationError(
                f"credential field '{name}' is not set"
            )
        return credentials[name]

    def _substitute(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in credentials:
            raise InterpolationError(
                f"credential field '{name}' is not set"
            )
        value = credentials[name]
        return "" if value is None else str(value)

    contains_placeholder = bool(_PATTERN.search(template))
    if contains_placeholder and template.startswith("="):
        body = template[1:]
        return _PATTERN.sub(_substitute, body)
    return _PATTERN.sub(_substitute, template)


def resolve_deep(obj: Any, credentials: dict[str, Any]) -> Any:
    """Recursive variant of :func:`resolve` for nested dicts and lists."""

    if isinstance(obj, dict):
        return {k: resolve_deep(v, credentials) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_deep(v, credentials) for v in obj]
    if isinstance(obj, tuple):
        return tuple(resolve_deep(v, credentials) for v in obj)
    return resolve(obj, credentials)


__all__ = ["InterpolationError", "resolve", "resolve_deep"]
