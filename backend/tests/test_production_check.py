"""Boot-time hardening validator (HANDOFF #2 / ADR-016 §8.4).

Locks in the contract for ``app.security.production_check``: dev
settings produce warnings, production with any insecure default raises.
A regression that loosens the validator silently lets us ship a build
where the first registered user becomes super_user.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.security.production_check import (
    collect_production_warnings,
    enforce_production_safety,
)

_LONG_ENOUGH_SECRET = "a" * 48  # >= 32 chars, opaque enough for the validator
_PROD_OVERRIDES = {
    "app_env": "production",
    "jwt_secret": _LONG_ENOUGH_SECRET,
    "cookie_secure": True,
    "allow_first_user_as_admin": False,
    "cors_allowed_origins": "https://app.example.com",
    "encryption_keys": "00" * 32,
}


def _make(**overrides: object) -> Settings:
    """Build a ``Settings`` instance with prod-safe defaults + overrides.

    Bypasses ``.env`` loading so the validator sees exactly what the
    test specified — otherwise local developer env leaks into assertions.
    """

    base = dict(_PROD_OVERRIDES)
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def test_clean_production_settings_pass() -> None:
    assert collect_production_warnings(_make()) == []
    enforce_production_safety(_make())  # must not raise


@pytest.mark.parametrize(
    "override, fragment",
    [
        ({"jwt_secret": ""}, "JWT_SECRET"),
        ({"jwt_secret": "short"}, "JWT_SECRET"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"allow_first_user_as_admin": True}, "ALLOW_FIRST_USER_AS_ADMIN"),
        ({"cors_allowed_origins": "http://localhost:3000"}, "CORS_ALLOWED_ORIGINS"),
        ({"cors_allowed_origins": ""}, "CORS_ALLOWED_ORIGINS"),
        ({"encryption_keys": ""}, "ENCRYPTION_KEYS"),
    ],
)
def test_each_insecure_default_is_flagged(
    override: dict[str, object], fragment: str
) -> None:
    issues = collect_production_warnings(_make(**override))
    assert any(fragment in msg for msg in issues), issues


def test_production_mode_refuses_to_boot_on_any_issue() -> None:
    settings = _make(cookie_secure=False)
    with pytest.raises(RuntimeError) as exc:
        enforce_production_safety(settings)
    assert "COOKIE_SECURE" in str(exc.value)
    assert "APP_ENV=dev" in str(exc.value)  # bypass hint preserved


def test_dev_mode_logs_but_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    settings = _make(app_env="dev", cookie_secure=False, jwt_secret="")
    with caplog.at_level("WARNING", logger="app.security.production_check"):
        enforce_production_safety(settings)
    messages = " ".join(r.message for r in caplog.records)
    assert "JWT_SECRET" in messages
    assert "COOKIE_SECURE" in messages


def test_mixed_local_and_real_origins_pass() -> None:
    """Localhost alongside a real origin is OK — common staging case."""

    settings = _make(
        cors_allowed_origins="https://app.example.com,http://localhost:3000"
    )
    issues = collect_production_warnings(settings)
    assert not any("CORS" in msg for msg in issues), issues


def test_ipv6_loopback_origin_is_classified_local() -> None:
    """``urlsplit`` strips IPv6 brackets — a bare ``split`` would mis-parse."""

    settings = _make(cors_allowed_origins="http://[::1]:3000")
    issues = collect_production_warnings(settings)
    assert any("CORS_ALLOWED_ORIGINS" in msg for msg in issues), (
        "::1 must be classified as loopback so production refuses to boot"
    )
