"""Export redaction (I-07): secrets never leave the instance in a company export.

* named secret columns and every ``*_encrypted`` column -> ``"<redacted>"``
  (``tenant_data_scope.secret_columns``); ``users`` rows keep an allowlist only;
* inside JSON values (tool/skill/agent configs, metadata): values under a secret-looking
  key, matched on whole words of the snake-cased key (``apiKey`` -> ``api_key``), never
  substrings (``max_output_tokens``/``token_count`` stay); ``{name, value}`` header
  pairs redact the value when the name is secret;
* every URL string: userinfo (``user:pass@``) and the query string are dropped.
"""

import re
from collections.abc import Mapping
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import Table

from app.services.platform.tenant_data_scope import REDACTED, secret_columns

USER_COLUMNS: Final = frozenset({"id", "email", "name", "is_active", "created_at"})
_SECRET_WORDS: Final = frozenset(
    {"password", "passwd", "secret", "token", "authorization", "cookie", "bearer", "apikey"}
)
_SECRET_PAIRS: Final = frozenset({("api", "key"), ("private", "key"), ("access", "key")})
_SAFE_LAST: Final = frozenset({"count", "counts", "tokens", "limit", "id", "ids", "type"})
_CAMEL: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_URL: Final = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]{0,15}://")
_NAME_KEYS: Final = ("name", "key", "header")


def _words(key: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", _CAMEL.sub("_", key).lower()) if part]


def secret_key(key: str) -> bool:
    words = _words(key)
    if not words:
        return False
    if any(pair in _SECRET_PAIRS for pair in zip(words, words[1:], strict=False)):
        return True
    if words[-1] in _SAFE_LAST:
        return False
    return any(word in _SECRET_WORDS for word in words)


def clean_url(value: str) -> str:
    if not _URL.match(value):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return REDACTED
    netloc = parts.netloc.rsplit("@", 1)[-1]
    if netloc == parts.netloc and not parts.query:
        return value
    return urlunsplit((parts.scheme, netloc, parts.path, "", parts.fragment))


def _pair_secret(value: Mapping[str, Any]) -> bool:
    names = [value[k] for k in _NAME_KEYS if isinstance(value.get(k), str)]
    return "value" in value and any(secret_key(name) for name in names)


def clean_json(value: Any) -> Any:
    if isinstance(value, str):
        return clean_url(value)
    if isinstance(value, list | tuple):
        return [clean_json(item) for item in value]
    if isinstance(value, Mapping):
        hide_value = _pair_secret(value)
        return {
            key: (
                REDACTED
                if (secret_key(str(key)) or (hide_value and key == "value")) and val is not None
                else clean_json(val)
            )
            for key, val in value.items()
        }
    return value


def clean_row(table: Table, row: Mapping[str, Any]) -> dict[str, Any]:
    hidden = secret_columns(table)
    out: dict[str, Any] = {}
    for key, value in row.items():
        if table.name == "users" and key not in USER_COLUMNS:
            continue
        out[key] = REDACTED if key in hidden and value is not None else clean_json(value)
    return out
