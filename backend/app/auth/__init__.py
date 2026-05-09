"""Auth primitives — password hashing, JWT, cookie helpers.

ADR-016 reference. The auth package keeps cryptographic primitives
isolated from the service layer so swapping algorithms (e.g. bcrypt →
argon2, HS256 → RS256) only touches one module.
"""

from app.auth.cookies import clear_auth_cookies, set_auth_cookies
from app.auth.jwt import (
    TokenPayload,
    TokenType,
    create_access_token,
    create_csrf_token,
    create_refresh_token,
    decode_token,
)
from app.auth.password import hash_password, verify_password

__all__ = [
    "TokenPayload",
    "TokenType",
    "clear_auth_cookies",
    "create_access_token",
    "create_csrf_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "set_auth_cookies",
    "verify_password",
]
