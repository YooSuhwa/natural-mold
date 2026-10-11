"""Credential service boundaries require exact scope, independently of routers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import Mock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.platform_scope import PlatformScope
from app.credentials import service
from app.credentials.creation import CredentialCreation
from app.credentials.mutation_scope import (
    CredentialAuditQuery,
    CredentialPatch,
    CredentialTestRecord,
)
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.models.user import User

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.mark.parametrize("operation", ["update", "test", "audit"])
async def test_credential_service_denies_foreign_scope_before_read_or_write(
    db: AsyncSession, private_scopes: PrivateScopes, operation: Literal["update", "test", "audit"]
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    credential = await service.create(
        db, CredentialCreation("http_bearer", "Original A", {"token": "B06_DUMMY_ONLY"}), own
    )
    await db.commit()
    with pytest.MonkeyPatch.context() as patches:
        encrypt = Mock(wraps=service.encrypt_data)
        patches.setattr(service, "encrypt_data", encrypt)
        match operation:
            case "update":
                pending = service.update(
                    db,
                    CredentialPatch(
                        credential.id, own.user_id, name="Changed B", data={"token": "Dummy change"}
                    ),
                    foreign,
                )
            case "test":
                pending = service.record_test(
                    db, CredentialTestRecord(credential.id, own.user_id, {"success": True}), foreign
                )
            case "audit":
                pending = service.list_audit_logs(db, CredentialAuditQuery(credential.id), foreign)
            case unreachable:
                assert_never(unreachable)
        with pytest.raises(AppError) as captured:
            await pending
        assert captured.value.status == 404
        encrypt.assert_not_called()
    assert credential.name == "Original A" and credential.last_tested_at is None


async def test_credential_mutation_uses_explicit_scope_and_fresh_membership(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    own, foreign = private_scopes.first, private_scopes.second
    credential = await service.create(
        db, CredentialCreation("http_bearer", "Original A", {"token": "B06_DUMMY_ONLY"}), own
    )
    user = await db.get(User, own.user_id)
    assert user is not None
    user.last_active_org_id = foreign.org_id
    await db.commit()
    patch = CredentialPatch(credential.id, own.user_id, name="Owned A")
    with pytest.raises(AppError) as platform:
        await service.update(db, patch, PlatformScope())
    assert platform.value.status == 404 and credential.name == "Original A"
    updated = await service.update(db, patch, own)
    assert updated.name == "Owned A"
    await service.record_test(
        db, CredentialTestRecord(credential.id, own.user_id, {"success": True}), own
    )
    logs = await service.list_audit_logs(db, CredentialAuditQuery(credential.id), own)
    assert {log.action for log in logs} >= {"create", "update", "test"}
    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id
        )
    )
    await db.commit()
    with pytest.raises(AppError):
        await service.update(db, CredentialPatch(credential.id, own.user_id, name="Revoked"), own)
    assert credential.name == "Owned A"
