"""Resolve public file-event pointers without reading artifact or image contents."""

from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.artifact_scope import readable_artifact_predicate
from app.authz.org_context import OrgContext
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact


async def readable_artifact_versions(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    run_id: UUID,
) -> frozenset[tuple[str, str]]:
    """Return current file/version pairs for an already authorized private run.

    The current private conversation/run guard is required on every read batch.
    Artifacts also have their own owner and version binding. The canonical file
    predicate checks the live parent delegation and lineage for an independent job.
    """
    artifact = ConversationArtifact
    version = ArtifactVersion
    filters = (
        artifact.conversation_id == conversation_id,
        artifact.assistant_msg_id == str(run_id),
        artifact.status != "deleted",
    )
    scope = await readable_artifact_predicate(db, context, *filters)
    # Old records can lack an explicit current-version pointer. As in the private
    # file GET, only a null pointer falls back to the latest version of that file.
    latest = (
        select(version.id)
        .where(version.artifact_id == artifact.id)
        .order_by(version.version_number.desc())
        .limit(1)
        .correlate(artifact)
        .scalar_subquery()
    )
    query = (
        select(artifact.id, version.id)
        .join(
            version,
            and_(
                version.artifact_id == artifact.id,
                version.id == func.coalesce(artifact.current_version_id, latest),
            ),
        )
        .where(*filters, scope)
        .execution_options(autoflush=False)
    )
    return frozenset((str(file), str(current)) for file, current in (await db.execute(query)))
