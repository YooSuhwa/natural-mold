"""Two-company graph for the I-07 export/purge tests (SQLite, no FK enforcement).

Each company gets members, the B08 core + history rows (agents, subagents, triggers,
runs, inputs, Agent API, conversations, events, attachments, artifacts, share links,
MCP server/tool/binding, token usage) and a credential, a text skill on disk, an audit
row and a finished export archive, so the purge has to walk FK children, NULL-tenant
rows and files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.audit_event import AuditEvent
from app.models.credential import Credential
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem, MarketplaceVersion
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.refresh_token import RefreshToken
from app.models.skill import Skill
from app.models.tenant_export import TenantExport
from app.models.user import User
from tests.authz.agent_lifecycle_fixture import LifecycleActors, LifecycleGraph
from tests.authz.agent_lifecycle_graph_history import seed_history_rows
from tests.authz.agent_lifecycle_graph_rows import seed_core_rows


@dataclass(frozen=True, slots=True)
class SeededCompany:
    tenant_id: UUID
    org_id: UUID
    slug: str
    owner_ids: tuple[UUID, UUID]
    skill_dir: Path
    attachment_path: Path
    export_path: Path


def _user(label: str) -> User:
    return User(
        id=uuid4(),
        email=f"{label}-{uuid4().hex[:6]}@i07.test",
        name=label,
        hashed_password="unused",
        is_active=True,
    )


def _graph(actors: LifecycleActors, root: Path) -> LifecycleGraph:
    return LifecycleGraph(
        tenant_id=actors.tenant_id,
        org_id=actors.org_id,
        root_owner_id=actors.owner_one_id,
        child_owner_id=actors.owner_two_id,
        root_agent_id=uuid4(),
        child_agent_ids=(uuid4(), uuid4()),
        conversation_ids=(uuid4(), uuid4(), uuid4()),
        run_ids=(uuid4(), uuid4(), uuid4()),
        input_ids=(uuid4(), uuid4(), uuid4()),
        attachment_id=uuid4(),
        artifact_id=uuid4(),
        artifact_version_ids=(uuid4(), uuid4()),
        api_run_id=uuid4(),
        trigger_run_id=uuid4(),
        share_link_id=uuid4(),
        mcp_binding_id=uuid4(),
        checkpoint_ids=(f"i07-root-{uuid4().hex}", f"i07-branch-{uuid4().hex}"),
        attachment_path=root / "attachment.bin",
        artifact_version_paths=(root / "artifact-v1.bin", root / "artifact-v2.bin"),
    )


async def _extras(db: AsyncSession, actors: LifecycleActors, root: Path) -> tuple[Path, Path]:
    tenant, org, owner = actors.tenant_id, actors.org_id, actors.owner_one_id
    skill_id = uuid4()
    skill_dir = root / "skills" / str(skill_id)
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: i07\n---\nbody\n")
    export_path = root / "exports" / "archive.zip"
    export_path.parent.mkdir(parents=True)
    export_path.write_bytes(b"PK")
    now = datetime.now(UTC)
    db.add_all(
        [
            Credential(
                tenant_id=tenant,
                org_id=org,
                user_id=owner,
                name="I07 key",
                definition_key="http_bearer",
                data_encrypted="ciphertext-secret",
                key_id="fixture",
                field_keys=["token"],
            ),
            Skill(
                id=skill_id,
                tenant_id=tenant,
                org_id=org,
                user_id=owner,
                name="I07 skill",
                slug=f"i07-{skill_id.hex[:8]}",
                kind="text",
                storage_path=str(skill_dir / "SKILL.md"),
            ),
            AuditEvent(
                tenant_id=tenant,
                org_id=org,
                actor_type="user",
                action="agent.create",
                target_type="agent",
                outcome="success",
            ),
            TenantExport(
                tenant_id=tenant,
                status="succeeded",
                progress=100,
                storage_path=str(export_path),
                expires_at=now + timedelta(days=7),
            ),
            *(
                RefreshToken(
                    user_id=user_id,
                    token_hash=uuid4().hex + uuid4().hex,
                    expires_at=now + timedelta(days=30),
                )
                for user_id in (actors.owner_one_id, actors.owner_two_id)
            ),
        ]
    )
    await db.commit()
    return skill_dir, export_path


async def seed_company(db: AsyncSession, root: Path, label: str) -> SeededCompany:
    tenant_id, org_id = uuid4(), uuid4()
    slug = f"i07-{label}-{tenant_id.hex[:6]}"
    one, two = _user(f"{label}-one"), _user(f"{label}-two")
    db.add(Tenant(id=tenant_id, name=f"I07 {label}", slug=slug, secrets_namespace=slug))
    db.add_all([one, two])
    await db.flush()
    db.add(Organization(id=org_id, tenant_id=tenant_id, name="Main", slug="main", is_default=True))
    await db.flush()
    for user in (one, two):
        db.add(TenantMember(tenant_id=tenant_id, user_id=user.id))
        db.add(OrganizationMember(org_id=org_id, user_id=user.id, tenant_id=tenant_id))
    one.last_active_org_id = org_id
    await db.commit()
    spare = [uuid4() for _ in range(6)]
    actors = LifecycleActors(
        one.id,
        two.id,
        spare[0],
        spare[1],
        spare[2],
        spare[3],
        spare[4],
        spare[5],
        tenant_id,
        org_id,
    )
    graph = _graph(actors, root / label)
    await seed_core_rows(db, actors, graph)
    await seed_history_rows(db, actors, graph)
    for model, run_id in ((AgentTriggerRun, graph.trigger_run_id), (AgentApiRun, graph.api_run_id)):
        await db.execute(update(model).where(model.id == run_id).values(tenant_id=tenant_id))
    await db.commit()
    skill_dir, export_path = await _extras(db, actors, root / label)
    return SeededCompany(
        tenant_id, org_id, slug, (one.id, two.id), skill_dir, graph.attachment_path, export_path
    )


async def share_member(db: AsyncSession, user_id: UUID, company: SeededCompany) -> None:
    """Make ``user_id`` a member of a second company (keeps its sessions on purge)."""
    db.add(TenantMember(tenant_id=company.tenant_id, user_id=user_id))
    db.add(OrganizationMember(org_id=company.org_id, user_id=user_id, tenant_id=company.tenant_id))
    await db.commit()


@dataclass(frozen=True, slots=True)
class CrossInstall:
    item_id: UUID
    installation_id: UUID
    installed_skill_id: UUID


async def install_across(
    db: AsyncSession, publisher: SeededCompany, installer: SeededCompany
) -> CrossInstall:
    """``installer`` installs a public skill item published by ``publisher``."""
    item_id, version_id, skill_id, installation_id = uuid4(), uuid4(), uuid4(), uuid4()
    owner = publisher.owner_ids[0]
    db.add(
        MarketplaceItem(
            id=item_id,
            tenant_id=publisher.tenant_id,
            org_id=publisher.org_id,
            resource_type="skill",
            owner_user_id=owner,
            name="Shared skill",
            slug=f"shared-{item_id.hex[:8]}",
            visibility="public",
            status="published",
            is_listed=True,
        )
    )
    await db.flush()
    db.add(
        MarketplaceVersion(
            id=version_id,
            item_id=item_id,
            version_label="1.0.0",
            version_number=1,
            resource_type="skill",
            payload_kind="skill_package",
            payload={},
            content_hash="0" * 64,
        )
    )
    await db.flush()
    db.add(
        Skill(
            id=skill_id,
            tenant_id=installer.tenant_id,
            org_id=installer.org_id,
            user_id=installer.owner_ids[0],
            name="Installed copy",
            slug=f"copy-{skill_id.hex[:8]}",
            kind="text",
            source_marketplace_item_id=item_id,
            source_marketplace_version_id=version_id,
        )
    )
    await db.flush()
    db.add(
        MarketplaceInstallation(
            id=installation_id,
            tenant_id=installer.tenant_id,
            org_id=installer.org_id,
            user_id=installer.owner_ids[0],
            item_id=item_id,
            version_id=version_id,
            resource_type="skill",
            installed_skill_id=skill_id,
            install_status="active",
        )
    )
    await db.commit()
    return CrossInstall(item_id, installation_id, skill_id)
