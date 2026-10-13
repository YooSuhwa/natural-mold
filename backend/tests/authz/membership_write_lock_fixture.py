"""Shared values for membership writer lock-order tests."""

from uuid import UUID

from fastapi import Request

from app.models.organization import Organization, Tenant
from app.models.organization_group import OrgInvitation

type InvitationScalarResult = Tenant | Organization | OrgInvitation | None
type RegistrationScalarResult = UUID | Tenant | Organization | OrgInvitation | None


def audit_request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/auth/register",
            "raw_path": b"/api/auth/register",
            "query_string": b"",
            "headers": [],
            "client": None,
            "server": ("test", 80),
            "state": {},
        }
    )
