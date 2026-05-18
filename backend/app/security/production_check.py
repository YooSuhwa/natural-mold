"""Boot-time hardening checks for production deployments.

Centralises the list of settings that are *fine for local dev* but
*dangerous in production* so the worst defaults can never silently ship.
The validator is pure (takes a ``Settings`` instance, returns a list of
errors) — the lifespan hook is a thin wrapper that raises on errors
when ``APP_ENV=production`` and logs warnings otherwise.

See HANDOFF #2 (operator env setup) and ADR-016 §8.4.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from app.auth.jwt import MIN_JWT_SECRET_LEN
from app.config import Settings

logger = logging.getLogger(__name__)

# Origins permitted in dev defaults — production must replace these.
# Includes the IPv6 loopback (``::1``) and any-iface (``::``) variants so
# they aren't accidentally treated as legitimate prod origins.
_LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}


def _is_local_only(origins: list[str]) -> bool:
    """True when every origin's hostname is a loopback variant.

    Uses ``urlsplit`` so IPv6 brackets, port numbers, and missing
    schemes are parsed correctly — a bare ``split("//")`` mis-classifies
    schemeless inputs and IPv6 literals.
    """

    if not origins:
        return True
    return all(
        (urlsplit(o).hostname or "").lower() in _LOCAL_ORIGIN_HOSTS
        for o in origins
    )


def collect_production_warnings(settings: Settings) -> list[str]:
    """Return a list of human-readable hardening issues. Empty ⇒ production-safe."""

    issues: list[str] = []

    if len(settings.jwt_secret) < MIN_JWT_SECRET_LEN:
        issues.append(
            "JWT_SECRET is empty or shorter than 32 chars. Generate with "
            "`python -c 'import secrets; print(secrets.token_urlsafe(48))'` "
            "and persist it — tokens issued by the ephemeral fallback are "
            "invalidated on every restart."
        )

    if not settings.cookie_secure:
        issues.append(
            "COOKIE_SECURE=false. Set true so browsers refuse to send "
            "auth cookies over plain HTTP."
        )

    if settings.allow_first_user_as_admin:
        issues.append(
            "ALLOW_FIRST_USER_AS_ADMIN=true. Turn it off once your operator "
            "account exists — otherwise the first stranger to /api/auth/register "
            "becomes super_user."
        )

    if _is_local_only(settings.cors_origins_list):
        issues.append(
            "CORS_ALLOWED_ORIGINS contains only localhost. Add every "
            "production frontend origin (comma-separated, scheme + host); "
            "browsers reject wildcards when credentials are sent."
        )

    if not settings.encryption_keys:
        issues.append(
            "ENCRYPTION_KEYS is empty. Credential creation will be rejected "
            "and any rotation job is a no-op. Generate one with "
            "`python -c 'import secrets; print(secrets.token_hex(32))'`."
        )

    return issues


def enforce_production_safety(settings: Settings) -> None:
    """Raise ``RuntimeError`` if ``APP_ENV=production`` and any issue exists.

    In ``dev`` we only emit warnings — the goal is to mirror what the
    operator will see in production without blocking local startup.
    """

    issues = collect_production_warnings(settings)
    if not issues:
        return

    if settings.app_env == "production":
        bullet_list = "\n".join(f"  - {msg}" for msg in issues)
        raise RuntimeError(
            "Refusing to start with insecure production settings:\n"
            f"{bullet_list}\n"
            "Fix the above and restart, or set APP_ENV=dev to bypass "
            "(local development only)."
        )

    for msg in issues:
        logger.warning("[dev] production hardening hint: %s", msg)
