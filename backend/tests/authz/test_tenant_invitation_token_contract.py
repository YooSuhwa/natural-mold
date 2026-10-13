"""Company invitation token purpose and hash-only response contracts."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.auth.jwt import create_org_invitation_token, create_tenant_invitation_token
from app.config import settings
from app.models.tenant_invitation import TenantInvitation
from app.services.tenants.invitations import creation_view


def test_company_token_is_deterministic_and_purpose_separated() -> None:
    invitation_id = uuid4()
    first = create_tenant_invitation_token(invitation_id)
    second = create_tenant_invitation_token(invitation_id)
    organization = create_org_invitation_token(invitation_id)
    assert first == second
    assert re.fullmatch(r"tv1\.[0-9a-f]{32}\.[0-9a-f]{64}", first)
    assert organization.startswith("v1.")
    assert first.removeprefix("tv1.") != organization.removeprefix("v1.")


def test_signing_rotation_never_substitutes_a_new_plaintext_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitation_id = uuid4()
    token = create_tenant_invitation_token(invitation_id)
    now = datetime.now(UTC)
    row = TenantInvitation(
        id=invitation_id,
        tenant_id=uuid4(),
        email="rotation-company@example.com",
        role=None,
        status="pending",
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_by=None,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    assert creation_view(row).token == token
    monkeypatch.setattr(settings, "jwt_secret", "dummy-rotated-secret-value-long-enough-42")
    assert creation_view(row).token is None
