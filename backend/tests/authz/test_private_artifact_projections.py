"""Owned artifact summaries must not project a same-org peer's private Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.organization import OrganizationMember, TenantMember
from app.models.user import User
from app.services.artifacts.library import list_library_artifacts, set_artifact_favorite

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.test_private_artifact_scope"]
Projection = Literal["library", "favorite"]


@dataclass(frozen=True, slots=True)
class ProjectionCase:
    artifact: ConversationArtifact
    context: OrgContext


@pytest.fixture
async def artifact_projection_case(
    db: AsyncSession, private_artifact: ConversationArtifact, private_scopes: PrivateScopes
) -> ProjectionCase:
    context = private_scopes.first
    peer = User(id=uuid4(), email="artifact-peer@example.com", name="Peer")
    db.add(peer)
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=context.tenant_id, user_id=peer.id),
            OrganizationMember(tenant_id=context.tenant_id, org_id=context.org_id, user_id=peer.id),
        ]
    )
    peer_agent = Agent(
        user_id=peer.id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        model_id=await db.scalar(
            select(Agent.model_id).where(Agent.id == private_artifact.agent_id)
        ),
        name="Peer private name",
        system_prompt="Fixture",
    )
    db.add(peer_agent)
    await db.flush()
    version = ArtifactVersion(
        artifact_id=private_artifact.id,
        version_number=1,
        object_key="fixture.txt",
        original_filename="fixture.txt",
        size_bytes=7,
        sha256="a" * 64,
    )
    db.add(version)
    await db.flush()
    private_artifact.current_version_id = version.id
    private_artifact.agent_id = peer_agent.id
    await db.commit()
    return ProjectionCase(private_artifact, context)


@pytest.mark.parametrize("projection", ["library", "favorite"])
async def test_private_artifact_excludes_peer_agent_name(
    db: AsyncSession, artifact_projection_case: ProjectionCase, projection: Projection
) -> None:
    case = artifact_projection_case
    match projection:
        case "library":
            page = await list_library_artifacts(
                db,
                context=case.context,
                q=None,
                agent_id=None,
                conversation_id=None,
                kind=None,
                favorite=None,
                limit=20,
                cursor=None,
            )
            assert len(page.items) == 1
            assert page.items[0].id == case.artifact.id
            assert page.items[0].agent_name is None
        case "favorite":
            summary = await set_artifact_favorite(
                db, context=case.context, artifact_id=case.artifact.id, is_favorite=True
            )
            assert summary.id == case.artifact.id
            assert summary.agent_name is None
        case unreachable:
            assert_never(unreachable)
