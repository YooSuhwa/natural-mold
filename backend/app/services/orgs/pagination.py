"""Opaque UUID keyset positions are bound to one organization."""

import base64
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.authz.error_codes import authorization_error


class DirectoryCursor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    org_id: UUID
    position: UUID


def encode_cursor(org_id: UUID, position: UUID) -> str:
    raw = DirectoryCursor(org_id=org_id, position=position).model_dump_json().encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(raw: str | None, org_id: UUID) -> UUID | None:
    if raw is None:
        return None
    try:
        cursor = DirectoryCursor.model_validate_json(
            base64.b64decode(
                raw + "=" * (-len(raw) % 4),
                altchars=b"-_",
                validate=True,
            )
        )
    except ValueError as error:
        raise authorization_error("invalid_cursor") from error
    if cursor.org_id != org_id:
        raise authorization_error("invalid_cursor")
    return cursor.position
