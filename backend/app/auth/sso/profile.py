"""The protocol-neutral result of a successful IdP/directory authentication."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import JsonValue

from app.auth.sso.domains import normalize_email

CLAIM_DEFAULTS: Final = {"email": "email", "name": "name", "groups": "groups"}
_MAX_GROUPS: Final = 200


@dataclass(frozen=True, slots=True)
class SsoProfile:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool | None
    name: str | None
    groups: tuple[str, ...] = ()

    def summary(self) -> dict[str, JsonValue]:
        """What a test login shows the administrator (no tokens, no secrets)."""
        return {
            "subject": self.subject,
            "email": self.email,
            "email_verified": self.email_verified,
            "name": self.name,
            "groups": list(self.groups),
        }


def _claim_name(mapping: Mapping[str, JsonValue], key: str) -> str:
    value = mapping.get(key)
    return value if isinstance(value, str) and value else CLAIM_DEFAULTS[key]


def _groups(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item) for item in value if isinstance(item, str | int)))[
        :_MAX_GROUPS
    ]


def oidc_profile(
    issuer: str, claims: Mapping[str, Any], mapping: Mapping[str, JsonValue]
) -> SsoProfile:
    email = claims.get(_claim_name(mapping, "email"))
    name = claims.get(_claim_name(mapping, "name"))
    verified = claims.get("email_verified")
    return SsoProfile(
        issuer=issuer,
        subject=str(claims["sub"]),
        email=normalize_email(email) if isinstance(email, str) else None,
        email_verified=verified if isinstance(verified, bool) else None,
        name=name[:100] if isinstance(name, str) and name else None,
        groups=_groups(claims.get(_claim_name(mapping, "groups"))),
    )


def directory_groups(values: object) -> tuple[str, ...]:
    return _groups(values)
