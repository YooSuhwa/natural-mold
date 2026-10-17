"""Host patterns for credential destinations (plan 11.9, S24 validation rules).

A pattern is a bare host (``api.example.com``) or a wildcard on the leftmost label only
(``*.example.com``). Wildcards match on label boundaries and cover exactly one label:
``*.example.com`` matches ``a.example.com``, never ``example.com``,
``a.b.example.com`` or ``badexample.com``. No scheme, port or path; an exact IP only
when explicitly configured.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from app.config import settings

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_host(host: str) -> str:
    return host.strip().rstrip(".").lower()


def host_of(url: str) -> str:
    return normalize_host(urlsplit(url).hostname or "")


def host_matches(host: str, pattern: str) -> bool:
    host, pattern = normalize_host(host), normalize_host(pattern)
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        head, _, rest = host.partition(".")
        return bool(head) and rest == pattern[2:]
    return host == pattern


def host_allowed(host: str, patterns: Iterable[str]) -> bool:
    return any(host_matches(host, pattern) for pattern in patterns)


def _ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def validate_host_pattern(value: str) -> str:
    """Return the normalized pattern or raise ``ValueError`` (S24 rules).

    An explicitly configured exact IP address is accepted; never in a wildcard.
    """

    pattern = normalize_host(value).removeprefix("[").removesuffix("]")
    if _ip_literal(pattern):
        return pattern
    if not pattern or any(ch in pattern for ch in "/:@?#[] "):
        raise ValueError("host pattern must be a bare host name")
    labels = pattern.split(".")
    if labels[0] == "*":
        labels = labels[1:]
        if len(labels) < 2 or _ip_literal(".".join(labels)):
            raise ValueError("a wildcard needs at least two host labels after it")
    if not all(_LABEL.match(label) for label in labels):
        raise ValueError("host pattern has an invalid label")
    return pattern


def is_ip_literal(host: str) -> bool:
    return _ip_literal(normalize_host(host))


# Development only (``APP_ENV=dev``) and only while the setting is empty.
DEV_LOCAL_HOSTS = ("localhost", "127.0.0.1", "host.docker.internal")


def _host_list(raw: str) -> tuple[str, ...]:
    hosts = tuple(part for part in (normalize_host(p) for p in raw.split(",")) if part)
    if not hosts and settings.app_env == "dev":
        return DEV_LOCAL_HOSTS
    return hosts


def plaintext_allowed_hosts() -> tuple[str, ...]:
    """``CREDENTIAL_HTTP_ALLOWED_HOSTS``: hosts a credential may reach over plain http."""

    return _host_list(settings.credential_http_allowed_hosts or "")


def plaintext_allowed(host: str) -> bool:
    return host_allowed(host, plaintext_allowed_hosts())


def private_allowed(host: str) -> bool:
    """``OUTBOUND_PRIVATE_ALLOWED_HOSTS``: hosts exempt from the private-address block."""

    return host_allowed(host, _host_list(settings.outbound_private_allowed_hosts or ""))


__all__ = [
    "host_allowed",
    "host_matches",
    "host_of",
    "is_ip_literal",
    "normalize_host",
    "plaintext_allowed",
    "plaintext_allowed_hosts",
    "private_allowed",
    "validate_host_pattern",
]
