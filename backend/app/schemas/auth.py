"""Pydantic schemas for /api/auth endpoints (ADR-016 §5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

AvatarMode = Literal["auto", "initials", "image"]
EditableAvatarMode = Literal["auto", "initials"]
AvatarColor = Literal["mint", "sky", "violet", "amber", "rose", "slate"]


def _strip_controls(value: str) -> str:
    return "".join(ch for ch in value if ch.isprintable()).strip()


class RegisterRequest(BaseModel):
    """POST /api/auth/register payload."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str | None = Field(None, min_length=1, max_length=80)
    # Legacy web clients used ``name``. Keep accepting it while the frontend
    # switches copy to "display name".
    name: str | None = Field(None, min_length=1, max_length=100)

    @field_validator("display_name", "name")
    @classmethod
    def _clean_names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _strip_controls(value)
        return cleaned or None

    @model_validator(mode="after")
    def _require_display_name(self) -> RegisterRequest:
        if not self.display_name and not self.name:
            raise ValueError("display_name is required")
        return self

    @property
    def profile_display_name(self) -> str:
        return self.display_name or self.name or ""


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
    display_name: str | None = None
    avatar_mode: AvatarMode = "auto"
    avatar_initials: str | None = None
    avatar_color: AvatarColor = "mint"
    avatar_image_url: str | None = None
    is_super_user: bool
    is_active: bool = True
    created_at: datetime
    last_login_at: datetime | None = None


class ProfileUpdateRequest(BaseModel):
    """PATCH /api/auth/me/profile payload."""

    display_name: str | None = Field(default=None, max_length=80)
    avatar_mode: EditableAvatarMode | None = None
    avatar_initials: str | None = Field(default=None, max_length=4)
    avatar_color: AvatarColor | None = None

    @field_validator("display_name")
    @classmethod
    def _clean_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _strip_controls(value)
        return cleaned or None

    @field_validator("avatar_initials")
    @classmethod
    def _clean_avatar_initials(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _strip_controls(value)
        if not cleaned:
            return None
        if len(cleaned) > 2:
            raise ValueError("avatar_initials must be 1-2 characters")
        return cleaned


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
