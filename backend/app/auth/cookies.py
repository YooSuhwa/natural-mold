"""Auth cookie helpers — set/clear the access/refresh/CSRF cookie trio.

ADR-016 §5.1 — Three cookies are issued on register/login/refresh:

* ``moldy_at`` (access) — HttpOnly. JS cannot read it.
* ``moldy_rt`` (refresh) — HttpOnly. Sent only on the same origin.
* ``moldy_csrf`` (CSRF) — **NOT** HttpOnly. JS reads this and echoes it
  in the ``X-CSRF-Token`` header on every mutation. Server compares the
  header against the cookie (double-submit pattern).

``Path=/`` so all routes see the auth cookies. ``Secure`` is gated on
``settings.cookie_secure`` (false in dev HTTP, true in prod HTTPS).
"""

from __future__ import annotations

from fastapi import Response

from app.config import settings


def _common_kwargs(max_age: int, *, http_only: bool) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "path": "/",
        "max_age": max_age,
        "httponly": http_only,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    return kwargs


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    """Attach the three auth cookies to ``response``.

    Lifetimes match the JWT ``exp`` so a browser auto-evicts the cookie
    just as the server-side token expires — no zombie cookies in dev
    tools after logout.
    """

    response.set_cookie(
        settings.cookie_name_access,
        access_token,
        **_common_kwargs(settings.access_token_expire_minutes * 60, http_only=True),
    )
    response.set_cookie(
        settings.cookie_name_refresh,
        refresh_token,
        **_common_kwargs(settings.refresh_token_expire_days * 86400, http_only=True),
    )
    # CSRF is intentionally not HttpOnly — the SPA reads it from
    # ``document.cookie`` and echoes via ``X-CSRF-Token``.
    response.set_cookie(
        settings.cookie_name_csrf,
        csrf_token,
        **_common_kwargs(settings.csrf_token_expire_minutes * 60, http_only=False),
    )


def clear_auth_cookies(response: Response) -> None:
    """Expire all three auth cookies (Max-Age=0)."""

    for name in (
        settings.cookie_name_access,
        settings.cookie_name_refresh,
        settings.cookie_name_csrf,
    ):
        response.delete_cookie(
            name,
            path="/",
            domain=settings.cookie_domain,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
        )
