"""LDAP password check for one connection → ``SsoProfile`` (plan 10.10).

Same user name: at most 10 attempts per 5 minutes per connection (in-process
window; the per-IP slowapi limit rides on the route). Every failure the caller can
see is the same ``invalid_credentials`` (no user / wrong password / empty password).
"""

import time
from collections import OrderedDict, deque
from typing import Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.domains import normalize_email
from app.auth.sso.errors import sso_error
from app.auth.sso.ldap import (
    LdapSettings,
    LdapUnavailable,
    attribute_names,
    checked_addresses,
    directory,
    require_encrypted_transport,
    user_search_filter,
)
from app.auth.sso.profile import SsoProfile
from app.auth.sso.secret_box import open_secret
from app.models.organization_identity import SsoConnection

WINDOW_SECONDS: Final = 300.0
MAX_ATTEMPTS: Final = 10
_MAX_KEYS: Final = 10_000
_attempts: OrderedDict[tuple[UUID, str], deque[float]] = OrderedDict()


def _throttle(connection_id: UUID, username: str) -> None:
    key, now = (connection_id, username.casefold()), time.monotonic()
    window = _attempts.setdefault(key, deque())
    _attempts.move_to_end(key)
    while window and now - window[0] > WINDOW_SECONDS:
        window.popleft()
    if len(window) >= MAX_ATTEMPTS:
        raise sso_error("login_rate_limited")
    window.append(now)
    while len(_attempts) > _MAX_KEYS:
        _attempts.popitem(last=False)


def reset_throttle() -> None:
    _attempts.clear()


async def ldap_settings(db: AsyncSession, connection: SsoConnection) -> LdapSettings:
    require_encrypted_transport(connection.ldap_server_url, connection.ldap_start_tls)
    password = await open_secret(db, connection)
    if not (connection.ldap_server_url and connection.ldap_bind_dn and password):
        raise sso_error("sso_config_invalid")
    addresses = await checked_addresses(connection.ldap_server_url)
    return LdapSettings(
        addresses=addresses,
        server_url=connection.ldap_server_url,
        start_tls=connection.ldap_start_tls,
        bind_dn=connection.ldap_bind_dn,
        bind_password=password,
        user_base_dn=connection.ldap_user_base_dn or "",
        group_base_dn=connection.ldap_group_base_dn,
        group_filter=connection.ldap_group_filter,
        attributes=attribute_names(connection.ldap_attribute_mapping or {}),
        ca_certificate=connection.ldap_ca_certificate,
    )


async def ldap_profile(
    db: AsyncSession, connection: SsoConnection, username: str, password: str
) -> SsoProfile:
    username = username.strip()
    if not username or len(username) > 256 or not password or len(password) > 1024:
        raise sso_error("invalid_credentials")  # an empty password is an anonymous bind
    _throttle(connection.id, username)
    config = await ldap_settings(db, connection)
    search = user_search_filter(connection.ldap_user_filter or "", username)
    try:
        entry = await directory().authenticate(config, search, password)
    except LdapUnavailable as error:
        raise sso_error("ldap_unavailable") from error
    if entry is None or not entry.subject:
        raise sso_error("invalid_credentials")
    return SsoProfile(
        issuer=connection.issuer,
        subject=entry.subject,
        email=normalize_email(entry.email) if entry.email else None,
        email_verified=None,
        name=entry.name[:100] if entry.name else None,
        groups=entry.groups,
    )
