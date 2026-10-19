"""LDAP/AD directory access behind an interface (plan 10.10 "LDAP·AD").

Login: the service account searches the user (filter with the RFC 4515-escaped user
name), then the user's own DN is bound with the typed password. The transport must
be ``ldaps://`` or ``ldap://`` + StartTLS; plaintext is refused before any I/O. The
password is never stored or logged. ``ldap3`` is not a project dependency yet, so the
default directory reports itself unavailable; deployments (and tests) install a real
``LdapDirectory`` with ``set_directory``.
"""

from dataclasses import dataclass, field
from typing import Final, Protocol
from urllib.parse import urlsplit

from pydantic import JsonValue

from app.auth.sso.errors import sso_error
from app.auth.sso.http import private_idps_allowed
from app.outbound.destination import OutboundBlockedError
from app.outbound.hosts import normalize_host, private_allowed
from app.outbound.transport import Resolver, public_addresses, system_resolver

_ESCAPES: Final = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\x00": r"\00"}
ATTRIBUTE_DEFAULTS: Final = {
    "subject": "entryUUID",
    "email": "mail",
    "name": "cn",
    "groups": "memberOf",
}


@dataclass(frozen=True, slots=True)
class LdapSettings:
    server_url: str
    start_tls: bool
    bind_dn: str
    bind_password: str
    user_base_dn: str
    group_base_dn: str | None
    group_filter: str | None
    attributes: dict[str, str]
    ca_certificate: str | None
    # Checked public addresses to dial (SSRF guard); empty = dial the host name.
    addresses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LdapEntry:
    dn: str
    subject: str
    email: str | None
    name: str | None
    groups: tuple[str, ...] = field(default=())


class LdapUnavailable(Exception):
    """The directory could not be reached or the client is not installed."""


class LdapDirectory(Protocol):
    async def authenticate(
        self, config: LdapSettings, user_filter: str, password: str
    ) -> LdapEntry | None:
        """The entry when exactly one user matches and its bind succeeds, else None."""
        ...


class UnavailableDirectory:
    async def authenticate(
        self, config: LdapSettings, user_filter: str, password: str
    ) -> LdapEntry | None:
        raise LdapUnavailable("no LDAP client is installed (ldap3 is not a dependency)")


_directory: LdapDirectory = UnavailableDirectory()
RESOLVER: Resolver = system_resolver  # test seam


def directory() -> LdapDirectory:
    return _directory


def set_directory(value: LdapDirectory) -> None:
    global _directory
    _directory = value


def escape_filter_value(value: str) -> str:
    return "".join(_ESCAPES.get(char, char) for char in value)


def require_encrypted_transport(server_url: str | None, start_tls: bool) -> None:
    url = (server_url or "").strip().lower()
    if url.startswith("ldaps://") or (url.startswith("ldap://") and start_tls):
        return
    raise sso_error("ldap_plaintext_refused")


async def checked_addresses(server_url: str) -> tuple[str, ...]:
    """Same policy as the OIDC client: public addresses only, unless the install is
    single-company or the host is in ``OUTBOUND_PRIVATE_ALLOWED_HOSTS``."""
    if private_idps_allowed():
        return ()
    url = urlsplit(server_url.strip())
    host = normalize_host(url.hostname or "")
    if not host:
        raise sso_error("sso_config_invalid")
    if private_allowed(host):
        return ()
    port = url.port or (636 if url.scheme.lower() == "ldaps" else 389)
    try:
        return tuple(await public_addresses(host, port, RESOLVER))
    except (OutboundBlockedError, OSError) as error:
        raise sso_error("ldap_unavailable") from error


def user_search_filter(template: str, username: str) -> str:
    return template.replace("{username}", escape_filter_value(username))


def attribute_names(mapping: dict[str, JsonValue]) -> dict[str, str]:
    return {
        key: value if isinstance(value := mapping.get(key), str) and value else default
        for key, default in ATTRIBUTE_DEFAULTS.items()
    }
