"""The default-credential uniqueness key includes organization as m81 requires."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.models.credential_default import CredentialDefault

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


async def test_default_credential_is_unique_within_one_org_scope(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    defaults: list[CredentialDefault] = []
    for context in (private_scopes.first, private_scopes.second):
        credential = await credential_service.create(
            db,
            CredentialCreation("openai", "Default fixture", {"api_key": "dummy-default"}),
            context,
        )
        default = CredentialDefault(
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            scope_kind="llm_model",
            scope_key="fixture",
            credential_id=credential.id,
        )
        db.add(default)
        defaults.append(default)
        await db.commit()
    with pytest.raises(IntegrityError):
        await _insert_duplicate_default(db, defaults[0])


async def _insert_duplicate_default(db: AsyncSession, default: CredentialDefault) -> None:
    async with db.begin_nested():
        db.add(
            CredentialDefault(
                user_id=default.user_id,
                org_id=default.org_id,
                tenant_id=default.tenant_id,
                scope_kind=default.scope_kind,
                scope_key=default.scope_key,
                credential_id=default.credential_id,
            )
        )
        await db.flush()
