"""Current organization cookie matches the authentication transport contract."""

from uuid import uuid4

import pytest
from fastapi import Response

from app.auth.cookies import ACTIVE_ORG_COOKIE, clear_auth_cookies, set_active_org_cookie
from app.config import settings


def test_active_org_cookie_uses_authentication_security_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an installation using secure, strict, domain-scoped auth cookies.
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "cookie_samesite", "strict")
    monkeypatch.setattr(settings, "cookie_domain", "moldy.example.com")
    response = Response()
    # When: the server sets the current organization.
    set_active_org_cookie(response, uuid4())
    # Then: transport attributes and lifetime match refresh authentication.
    cookie = response.headers["set-cookie"]
    assert all(
        token in cookie
        for token in (
            ACTIVE_ORG_COOKIE,
            "HttpOnly",
            "Secure",
            "SameSite=strict",
            "Path=/",
            "Domain=moldy.example.com",
            f"Max-Age={settings.refresh_token_expire_days * 86400}",
        )
    )


def test_logout_clears_active_org_cookie_with_matching_domain_and_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same domain-scoped secure cookie contract used during selection.
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "cookie_samesite", "strict")
    monkeypatch.setattr(settings, "cookie_domain", "moldy.example.com")
    response = Response()
    # When: logout clears all browser authentication state.
    clear_auth_cookies(response)
    # Then: the organization cookie's domain/path match its original attributes.
    cookie = next(
        value for value in response.headers.getlist("set-cookie") if ACTIVE_ORG_COOKIE in value
    )
    assert all(
        token in cookie
        for token in (
            "Max-Age=0",
            "Secure",
            "SameSite=strict",
            "Path=/",
            "Domain=moldy.example.com",
        )
    )
