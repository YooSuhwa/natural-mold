"""Company domain names and the DNS TXT ownership check (plan 10.10 "도메인 확인").

The resolver sits behind ``TxtResolver`` so tests (and closed networks with a custom
resolver) replace it; the default uses dnspython, already locked as a transitive
dependency.
"""

import re
import secrets
from typing import Final, Protocol

from app.auth.sso.errors import sso_error
from app.exceptions import AppError

TXT_PREFIX: Final = "moldy-verify="
_LABEL: Final = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class TxtResolver(Protocol):
    async def txt(self, domain: str) -> list[str]: ...


class DnsTxtResolver:
    async def txt(self, domain: str) -> list[str]:
        import dns.asyncresolver
        import dns.exception

        try:
            answer = await dns.asyncresolver.resolve(domain, "TXT", lifetime=5.0)
        except dns.exception.DNSException:
            return []
        values: list[str] = []
        for record in answer:
            strings = getattr(record, "strings", ())
            values.append(b"".join(strings).decode("utf-8", "replace"))
        return values


_resolver: TxtResolver = DnsTxtResolver()


def txt_resolver() -> TxtResolver:
    return _resolver


def set_txt_resolver(resolver: TxtResolver) -> None:
    global _resolver
    _resolver = resolver


def normalize_domain(raw: str) -> str:
    """Lower-case IDNA form; at least two labels, no wildcard, port or path."""
    candidate = raw.strip().rstrip(".").lower()
    try:
        ascii_form = candidate.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise sso_error("domain_invalid") from error
    labels = ascii_form.split(".")
    if len(ascii_form) > 253 or len(labels) < 2 or not all(map(_LABEL.match, labels)):
        raise sso_error("domain_invalid")
    if labels[-1].isdigit():
        raise sso_error("domain_invalid")
    return ascii_form


def email_domain(email: str) -> str | None:
    local, sep, domain = email.strip().rpartition("@")
    if not sep or not local:
        return None
    try:
        return normalize_domain(domain)
    except AppError:  # a malformed domain simply has no company
        return None


def normalize_email(email: str) -> str | None:
    """``local@idna-domain`` lower-cased (the users table stores emails lower-cased)."""
    local, sep, domain = email.strip().rpartition("@")
    if not sep or not local or any(ch.isspace() for ch in local):
        return None
    try:
        return f"{local.lower()}@{normalize_domain(domain)}"
    except AppError:
        return None


def new_txt_token() -> str:
    return secrets.token_hex(16)


async def txt_matches(domain: str, token: str) -> bool:
    expected = TXT_PREFIX + token
    records = await txt_resolver().txt(domain)
    return any(secrets.compare_digest(value.strip(), expected) for value in records)
