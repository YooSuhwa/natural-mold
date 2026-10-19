"""I-07 purge across companies: other companies keep what they installed, and an
outside reference the purge cannot detach stops it before any change."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.audit_event import AuditEvent
from app.models.marketplace import MarketplaceInstallation
from app.models.organization import Tenant
from app.models.share_link import ShareLink
from app.models.skill import Skill
from app.services.platform.tenant_data_scope import metadata
from app.services.platform.tenant_purge_job import run_tenant_purge_job
from tests.authz.i07_company_seed import SeededCompany, install_across, seed_company
from tests.conftest import TestSession

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)


async def _seed(tmp_path: Path) -> tuple[SeededCompany, SeededCompany]:
    async with TestSession() as db:
        publisher = await seed_company(db, tmp_path, "publisher")
        installer = await seed_company(db, tmp_path, "installer")
        tenant = await db.get(Tenant, publisher.tenant_id)
        assert tenant is not None
        tenant.status, tenant.deletion_due_at = "deletion_scheduled", NOW - timedelta(hours=1)
        await db.commit()
    return publisher, installer


async def test_installer_keeps_its_copy_and_installation_as_source_removed(
    tmp_path: Path,
) -> None:
    publisher, installer = await _seed(tmp_path)
    async with TestSession() as db:
        cross = await install_across(db, publisher, installer)

    [result] = await run_tenant_purge_job(maker=TestSession, now=NOW)

    assert result.detached_installations == 1
    async with TestSession() as db:
        installation = await db.get(MarketplaceInstallation, cross.installation_id)
        assert installation is not None
        assert (installation.item_id, installation.version_id) == (None, None)
        assert installation.install_status == "source_removed"
        assert installation.tenant_id == installer.tenant_id
        copy = await db.get(Skill, cross.installed_skill_id)
        assert copy is not None and copy.tenant_id == installer.tenant_id
        assert copy.source_marketplace_item_id is None
        event = await db.scalar(
            select(AuditEvent).where(AuditEvent.action == "marketplace.installation_source_removed")
        )
        assert event is not None and event.tenant_id == installer.tenant_id
        assert event.org_id == installer.org_id
        assert (event.event_metadata or {})["installation_ids"] == [str(cross.installation_id)]


async def test_a_non_nullable_outside_reference_aborts_before_any_change(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    publisher, installer = await _seed(tmp_path)
    async with TestSession() as db:
        conversation = await db.scalar(
            select(ShareLink.conversation_id).where(ShareLink.tenant_id == publisher.tenant_id)
        )
        db.add(
            ShareLink(
                tenant_id=installer.tenant_id,
                org_id=installer.org_id,
                share_token="i07-cross",
                conversation_id=conversation,
                created_by=installer.owner_ids[0],
            )
        )
        await db.commit()
        keys_before = (await db.get(Tenant, publisher.tenant_id)).data_keys  # type: ignore[union-attr]
    counts_before = await _counts()

    with caplog.at_level(logging.WARNING):
        assert await run_tenant_purge_job(maker=TestSession, now=NOW) == []

    assert "outside_reference" in caplog.text and "share_links.conversation_id=1" in caplog.text
    assert await _counts() == counts_before
    async with TestSession() as db:
        tenant = await db.get(Tenant, publisher.tenant_id)
        assert tenant is not None and tenant.data_keys == keys_before
    assert publisher.skill_dir.exists() and publisher.attachment_path.exists()


async def _counts() -> dict[str, int]:
    async with TestSession() as db:
        return {
            table.name: int(await db.scalar(select(func.count()).select_from(table)) or 0)
            for table in metadata().sorted_tables
        }
