"""Installation annotations never describe the same user's other organization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.marketplace import origin_service
from app.marketplace.install.common import _existing_installation
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplacePublicationLink,
    MarketplaceVersion,
)
from app.models.skill import Skill

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class InstalledFixture:
    item: MarketplaceItem
    skill: Skill


@pytest.fixture
async def installed_fixture(db: AsyncSession, private_scopes: PrivateScopes) -> InstalledFixture:
    context = private_scopes.first
    item = MarketplaceItem(
        resource_type="skill",
        name="System fixture",
        slug="system-install-fixture",
        is_system=True,
        owner_user_id=None,
        org_id=None,
        tenant_id=None,
        visibility="system",
        status="published",
    )
    skill = Skill(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Installed fixture",
        slug="installed-fixture",
        kind="text",
        storage_path="unused-install-fixture",
        credential_requirements=[],
    )
    db.add_all([item, skill])
    await db.flush()
    version = MarketplaceVersion(
        item_id=item.id,
        version_label="fixture-1",
        version_number=1,
        resource_type="skill",
        payload_kind="skill_package",
        content_hash="a" * 64,
        credential_requirements=[],
    )
    db.add(version)
    await db.flush()
    item.latest_version_id = version.id
    db.add(
        MarketplaceInstallation(
            item_id=item.id,
            version_id=version.id,
            resource_type="skill",
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            installed_skill_id=skill.id,
        )
    )
    await db.commit()
    return InstalledFixture(item, skill)


async def test_single_installation_annotation_is_scoped_to_selected_company(
    db: AsyncSession, installed_fixture: InstalledFixture, private_scopes: PrivateScopes
) -> None:
    own = await origin_service.derive_installation_summary(
        db, item=installed_fixture.item, context=private_scopes.first
    )
    assert own.installed is True
    foreign = await origin_service.derive_installation_summary(
        db, item=installed_fixture.item, context=private_scopes.second
    )
    assert foreign.installed is False and foreign.installation_id is None


async def test_bulk_installation_annotation_is_scoped_to_selected_company(
    db: AsyncSession, installed_fixture: InstalledFixture, private_scopes: PrivateScopes
) -> None:
    result = await origin_service.bulk_derive_installation_summaries(
        db, items=[installed_fixture.item], context=private_scopes.second
    )
    assert result[installed_fixture.item.id].installed is False


async def test_foreign_publication_annotation_is_absent(
    db: AsyncSession, installed_fixture: InstalledFixture, private_scopes: PrivateScopes
) -> None:
    # Given a publication linked to a private skill in organization A.
    context = private_scopes.first
    item = MarketplaceItem(
        owner_user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        resource_type="skill",
        name="Private publication",
        slug="private-publication",
        visibility="private",
        status="published",
    )
    db.add(item)
    await db.flush()
    db.add(
        MarketplacePublicationLink(
            item_id=item.id,
            source_skill_id=installed_fixture.skill.id,
            user_id=context.user_id,
            resource_type="skill",
        )
    )
    await db.flush()
    # When publication summaries are requested in organization B.
    single = await origin_service.derive_publication_summary_for_skill(
        db, installed_fixture.skill, private_scopes.second
    )
    bulk = await origin_service.bulk_derive_publication_summaries_for_skills(
        db, [installed_fixture.skill], private_scopes.second
    )
    # Then neither result includes the foreign publication or sharing count.
    assert single.state == "not_published" and single.item_id is None
    assert bulk[installed_fixture.skill.id].state == "not_published"


async def test_foreign_installed_child_is_absent_from_owned_installation_annotations(
    db: AsyncSession, installed_fixture: InstalledFixture, private_scopes: PrivateScopes
) -> None:
    # Given an own installation with a malformed child reference into another company.
    skill = installed_fixture.skill
    skill.org_id = private_scopes.second.org_id
    skill.tenant_id = private_scopes.second.tenant_id
    await db.flush()
    # When the installation's organization requests its catalog annotations.
    single = await origin_service.derive_installation_summary(
        db, item=installed_fixture.item, context=private_scopes.first
    )
    bulk = await origin_service.bulk_derive_installation_summaries(
        db, items=[installed_fixture.item], context=private_scopes.first
    )
    skill_annotations = await origin_service.bulk_derive_skill_installation_summaries(
        db, [skill], private_scopes.first
    )
    # Then the foreign private resource identity is absent from every projection.
    assert single.installed is False and single.installed_resource_id is None
    assert bulk[installed_fixture.item.id].installed is False
    assert skill_annotations.get(skill.id) is None


async def test_reuse_rejects_foreign_installed_child_before_materialization(
    db: AsyncSession, installed_fixture: InstalledFixture, private_scopes: PrivateScopes
) -> None:
    # Given a private installation pointing into another organization.
    installed_fixture.skill.org_id = private_scopes.second.org_id
    installed_fixture.skill.tenant_id = private_scopes.second.tenant_id
    await db.flush()
    context = private_scopes.first
    user = CurrentUser(id=context.user_id, email="fixture@example.com", name="Fixture")
    # When reuse tries to select that malformed installation.
    with pytest.raises(AppError) as rejected:
        await _existing_installation(db, item=installed_fixture.item, user=user, context=context)
    # Then the private dependency is denied before any materialization or mutation.
    assert rejected.value.status == 404


async def test_skill_installation_annotation_rejects_foreign_scope(
    db: AsyncSession, installed_fixture: InstalledFixture, private_scopes: PrivateScopes
) -> None:
    result = await origin_service.bulk_derive_skill_installation_summaries(
        db, [installed_fixture.skill], private_scopes.second
    )
    assert result[installed_fixture.skill.id] is None
