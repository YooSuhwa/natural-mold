"""Pydantic schemas for /api/auth endpoints (ADR-016 §5)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    """POST /api/auth/register payload."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(..., min_length=1, max_length=100)


class LoginRequest(BaseModel):
    """POST /api/auth/login payload."""

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class UserResponse(BaseModel):
    """User projection returned by auth endpoints + /me.

    Mirrors ``CurrentUser`` plus a few read-only audit fields. Excludes
    ``hashed_password`` and any reset/verify tokens — those never cross
    the API boundary.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    is_super_user: bool
    is_active: bool = True
    created_at: datetime
    last_login_at: datetime | None = None


class AuthResponse(BaseModel):
    """Body returned by register / login / refresh.

    Cookies carry the access + refresh JWTs (HttpOnly). The CSRF token is
    *also* shipped in a non-HttpOnly cookie (``moldy_csrf``) so the SPA
    can read it via ``document.cookie``; we additionally return it in the
    body for clients that prefer to keep it in JS memory.
    """

    user: UserResponse
    csrf_token: str


class RefreshResponse(BaseModel):
    """Body for /api/auth/refresh — minimal since cookies do the heavy lifting."""

    csrf_token: str


class LogoutResponse(BaseModel):
    ok: bool = True
