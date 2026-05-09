"""bcrypt password hashing — passlib CryptContext wrapper.

ADR-016 §3.1 — bcrypt is the only configured scheme. ``passlib`` lets us
add successors later (e.g. ``argon2``) by appending to ``schemes`` and
flipping ``deprecated="auto"`` so existing hashes auto-upgrade on next
verify. Keep this module thin: only hash + verify.
"""

from __future__ import annotations

# passlib 1.7.4 probes ``bcrypt.__about__.__version__`` which was removed
# in bcrypt 4.1. The probe failure is logged at ERROR but is otherwise
# non-fatal. Patch the missing attribute before passlib loads its bcrypt
# backend so noise stays out of test logs.
import bcrypt as _bcrypt

if not hasattr(_bcrypt, "__about__"):
    class _About:  # noqa: D401 — passlib only reads __version__
        __version__ = getattr(_bcrypt, "__version__", "4.0.0")

    _bcrypt.__about__ = _About()  # type: ignore[attr-defined]

from passlib.context import CryptContext  # noqa: E402 — must follow shim

# ``bcrypt__rounds=12`` matches the OWASP 2023 recommendation. Higher
# rounds slow login linearly — bump only after benchmarking on the actual
# production CPU profile.
_pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)


def hash_password(plain: str) -> str:
    """Return a bcrypt hash for ``plain``. Salt is generated per call."""

    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    """Return ``True`` iff ``plain`` matches ``hashed``.

    A ``None`` hash (OAuth-only user, password not set) yields ``False``
    without raising — callers want a uniform "wrong credentials" response,
    not a 500.
    """

    if not hashed:
        return False
    try:
        return _pwd_context.verify(plain, hashed)
    except ValueError:
        # Malformed hash — treat as failed verify, do not propagate.
        return False
