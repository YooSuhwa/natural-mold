"""Evaluate confirmed goal conditions against the completed run's stored output."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.authz.artifact_scope import private_artifact_predicate
from app.authz.conversation_scope import resolve_conversation_actor
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scope_predicate
from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_goal_criteria import (
    CsvTableCriterion,
    GoalCriterion,
    GoalEvidence,
    GoalEvidenceCheck,
    ManualCriterion,
    NonemptyFileCriterion,
    normalized_cell,
)
from app.services.artifact_storage import (
    ArtifactStorageBackendUnavailableError,
    get_artifact_storage_backend,
)
from app.services.artifacts.errors import ArtifactNotFoundError

_CRITERION = TypeAdapter(GoalCriterion)
MAX_GOAL_EVIDENCE_BYTES = 8 * 1024 * 1024
_NO_VALUE = {"", "-", "—", "n/a", "미확인", "미상"}


def _read_verified(path: Path, *, size: int, sha256: str) -> bytes:
    """Hash exactly the expected bytes, bounded even if the stored file has grown."""
    digest = hashlib.sha256()
    content = io.BytesIO()
    remaining = size
    with path.open("rb") as source:
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ArtifactNotFoundError("incomplete evidence")
            digest.update(chunk)
            content.write(chunk)
            remaining -= len(chunk)
        if source.read(1) or digest.hexdigest() != sha256:
            raise ArtifactNotFoundError("invalid evidence")
    return content.getvalue()


async def _verified_content(artifact: ConversationArtifact, version: ArtifactVersion) -> bytes:
    if (
        version.storage_provider != "local"
        or not 0 < version.size_bytes <= settings.artifact_max_bytes
    ):
        raise ArtifactNotFoundError("evidence not available")
    try:
        storage = get_artifact_storage_backend()
        path = await storage.local_path(object_key=version.object_key, tenant_id=artifact.tenant_id)
        expected_directory = (
            "conversations",
            str(artifact.conversation_id),
            str(artifact.id),
            f"v{version.version_number}",
        )
        if path.parent.parts[-4:] != expected_directory:
            raise ArtifactNotFoundError("evidence storage identity mismatch")
        return await asyncio.to_thread(
            _read_verified,
            path,
            size=version.size_bytes,
            sha256=version.sha256,
        )
    except (OSError, ValueError, ArtifactStorageBackendUnavailableError) as exc:
        raise ArtifactNotFoundError("evidence not available") from exc


async def read_goal_evidence_version(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    artifact_id: UUID,
    version_id: UUID,
) -> tuple[ArtifactVersion, bytes]:
    """Open the recorded version with today's membership, even after a later rewrite."""
    await require_private_actor(db, context)
    row = (
        await db.execute(
            select(ConversationArtifact, ArtifactVersion)
            .join(ArtifactVersion, ArtifactVersion.artifact_id == ConversationArtifact.id)
            .where(
                private_artifact_predicate(context),
                ConversationArtifact.conversation_id == conversation_id,
                ConversationArtifact.id == artifact_id,
                ConversationArtifact.status == "ready",
                ArtifactVersion.id == version_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise ArtifactNotFoundError("evidence not available")
    artifact, version = row
    return version, await _verified_content(artifact, version)


def _matches_filter(cell: str, expected: str) -> bool:
    # A source URL identifies a particular document. Paths, queries and
    # fragments can distinguish resources by case or by Unicode spelling.
    if expected.lower().startswith(("https://", "http://")):
        return cell.strip() == expected
    return normalized_cell(cell) == normalized_cell(expected)


def _check_table(criterion: CsvTableCriterion, data: bytes) -> str:
    delimiter = "\t" if criterion.logical_path.lower().endswith(".tsv") else ","
    try:
        reader = csv.reader(
            io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter=delimiter, strict=True
        )
        header = [normalized_cell(name) for name in next(reader, [])]
        if not header or "" in header or len(header) != len(set(header)):
            return "invalid_header"
        columns = {name: index for index, name in enumerate(header)}
        required = [normalized_cell(name) for name in criterion.required_columns]
        if any(name not in columns for name in required):
            return "missing_columns"
        indices = [columns[name] for name in required]
        key = columns[normalized_cell(criterion.unique_by)]
        filters = {columns[normalized_cell(k)]: v for k, v in criterion.column_equals.items()}
        references = {}
        for reference in criterion.reference_rows:
            expected = {columns[normalized_cell(k)]: value for k, value in reference.items()}
            references[normalized_cell(expected[key])] = expected
        seen: set[str] = set()
        for cells in reader:
            if not cells:
                continue
            if len(cells) != len(header):
                return "invalid_row"
            row = [normalized_cell(cell) for cell in cells]
            if any(row[index] in _NO_VALUE for index in indices):
                return "empty_cell"
            if any(not _matches_filter(cells[index], value) for index, value in filters.items()):
                return "column_mismatch"
            if row[key] in seen:
                return "duplicate_key"
            if references:
                expected = references.get(row[key])
                if expected is None or any(
                    cells[index].strip() != value for index, value in expected.items()
                ):
                    return "reference_mismatch"
            seen.add(row[key])
        if references and seen != references.keys():
            return "reference_mismatch"
        return "verified" if len(seen) >= criterion.minimum_rows else "insufficient_rows"
    except (UnicodeDecodeError, csv.Error):
        return "invalid_table"


@dataclass
class _EvidenceReads:
    """One terminal evaluation holds a shared execution lock; bound its total storage I/O."""

    remaining: int
    verified: dict[UUID, bytes | None] = field(default_factory=dict)

    async def read(
        self, artifact: ConversationArtifact, version: ArtifactVersion
    ) -> tuple[bytes | None, str]:
        if version.id not in self.verified:
            if version.size_bytes > self.remaining:
                return None, "evidence_too_large"
            self.remaining -= max(0, version.size_bytes)
            try:
                self.verified[version.id] = await _verified_content(artifact, version)
            except ArtifactNotFoundError:
                self.verified[version.id] = None
        data = self.verified[version.id]
        return data, "verified" if data is not None else "unreadable_version"


async def _inspect_file(
    criterion: CsvTableCriterion | NonemptyFileCriterion,
    check: GoalEvidenceCheck,
    artifact: ConversationArtifact,
    version: ArtifactVersion,
    reads: _EvidenceReads,
) -> None:
    needs_table = isinstance(criterion, CsvTableCriterion)
    if needs_table and version.size_bytes > settings.artifact_preview_max_text_bytes:
        check.reason = "table_too_large"
        return
    data, check.reason = await reads.read(artifact, version)
    if data is None:
        return
    check.source_run_id = UUID(artifact.assistant_msg_id)
    check.artifact_id = artifact.id
    check.version_id = version.id
    check.version_number = version.version_number
    check.sha256 = version.sha256
    check.reason = (
        await asyncio.to_thread(_check_table, criterion, data)
        if isinstance(criterion, CsvTableCriterion)
        else "verified"
    )
    check.passed = check.reason == "verified"


def _trusted_checks(previous_evidence: dict | None) -> dict[int, GoalEvidenceCheck]:
    if previous_evidence is None:
        return {}
    try:
        receipt = GoalEvidence.model_validate(previous_evidence)
    except ValidationError:
        return {}
    # Only the lifecycle service supplies these stored receipts. An older schema
    # without the producing run cannot establish provenance and must be rechecked.
    return {
        index: entry
        for index, entry in enumerate(receipt.checks)
        if entry.criterion_index == index
        and entry.passed
        and entry.reason == "verified"
        and entry.source_run_id
        and entry.artifact_id
        and entry.version_id
        and entry.sha256
    }


async def _prior_files(
    db: AsyncSession,
    *,
    context: OrgContext,
    run: ConversationRun,
    receipts: dict[int, GoalEvidenceCheck],
) -> dict[UUID, tuple[ConversationArtifact, ArtifactVersion]]:
    if not receipts:
        return {}
    origin_ids = set(
        await db.scalars(
            select(ConversationRun.id).where(
                private_scope_predicate(ConversationRun, context),
                ConversationRun.conversation_id == run.conversation_id,
                ConversationRun.id.in_([item.source_run_id for item in receipts.values()]),
                ConversationRun.id != run.id,
                ConversationRun.created_at <= run.created_at,
                ConversationRun.status == "completed",
                ConversationRun.is_active.is_(False),
            )
        )
    )
    newer = aliased(ConversationArtifact)
    replaced = (
        select(newer.id)
        .where(
            newer.conversation_id == ConversationArtifact.conversation_id,
            newer.user_id == context.user_id,
            newer.org_id == context.org_id,
            newer.tenant_id == context.tenant_id,
            newer.logical_path == ConversationArtifact.logical_path,
            newer.id != ConversationArtifact.id,
            newer.created_at >= ConversationArtifact.created_at,
        )
        .exists()
    )
    rows = await db.execute(
        select(ConversationArtifact, ArtifactVersion)
        .join(
            ArtifactVersion,
            and_(
                ArtifactVersion.id == ConversationArtifact.current_version_id,
                ArtifactVersion.artifact_id == ConversationArtifact.id,
            ),
        )
        .where(
            private_artifact_predicate(context),
            ConversationArtifact.conversation_id == run.conversation_id,
            ConversationArtifact.id.in_([entry.artifact_id for entry in receipts.values()]),
            ConversationArtifact.assistant_msg_id.in_([str(id_) for id_ in origin_ids]),
            ConversationArtifact.status == "ready",
            ~replaced,
        )
    )
    return {artifact.id: (artifact, version) for artifact, version in rows}


async def inspect_goal_evidence(
    db: AsyncSession,
    *,
    conversation: Conversation,
    run: ConversationRun,
    criteria: Sequence[object],
    previous_evidence: dict | None = None,
) -> GoalEvidence:
    """Use only the previous settled receipt from the same goal version and steer watermark.

    The lifecycle service selects that trusted receipt. We verify each producing run,
    exact current version, and bytes again. Fresh output on the same path wins,
    including deleted, failed or corrupt output.
    """
    context = await resolve_conversation_actor(db, conversation.id, conversation.user_id)
    finished = await db.scalar(
        select(ConversationRun.id)
        .where(
            ConversationRun.id == run.id,
            ConversationRun.conversation_id == conversation.id,
            private_scope_predicate(ConversationRun, context),
            ConversationRun.status == "completed",
            ConversationRun.is_active.is_(False),
        )
        .execution_options(autoflush=False)
    )
    checks: list[GoalEvidenceCheck] = []
    pending: list[tuple[GoalEvidenceCheck, CsvTableCriterion | NonemptyFileCriterion]] = []
    for index, raw in enumerate(criteria):
        check = GoalEvidenceCheck(criterion_index=index, reason="invalid_criterion")
        checks.append(check)
        try:
            criterion = _CRITERION.validate_python(raw)
        except ValidationError:
            continue
        if isinstance(criterion, ManualCriterion):
            check.reason = "manual_review"
            continue
        check.logical_path = criterion.logical_path
        check.reason = "run_unavailable" if finished is None else "artifact_missing"
        pending.append((check, criterion))
    if pending and finished is not None:
        rows = await db.execute(
            select(ConversationArtifact, ArtifactVersion)
            .outerjoin(
                ArtifactVersion,
                and_(
                    ArtifactVersion.id == ConversationArtifact.current_version_id,
                    ArtifactVersion.artifact_id == ConversationArtifact.id,
                ),
            )
            .where(
                private_artifact_predicate(context),
                ConversationArtifact.conversation_id == conversation.id,
                ConversationArtifact.assistant_msg_id == str(run.id),
                ConversationArtifact.logical_path.in_([item.logical_path for _, item in pending]),
            )
        )
        matches = {artifact.logical_path: (artifact, version) for artifact, version in rows}
        receipts = _trusted_checks(previous_evidence)
        prior = await _prior_files(db, context=context, run=run, receipts=receipts)
        reads = _EvidenceReads(MAX_GOAL_EVIDENCE_BYTES)
        for check, criterion in pending:
            if match := matches.get(criterion.logical_path):
                artifact, version = match
                if artifact.status == "ready" and version is not None:
                    await _inspect_file(criterion, check, artifact, version, reads)
                continue
            receipt = receipts.get(check.criterion_index)
            if receipt is None or receipt.artifact_id is None:
                continue
            candidate = prior.get(receipt.artifact_id)
            if candidate is None:
                continue
            artifact, version = candidate
            if (
                artifact.logical_path == receipt.logical_path == criterion.logical_path
                and artifact.assistant_msg_id == str(receipt.source_run_id)
                and version.id == receipt.version_id
                and version.version_number == receipt.version_number
                and version.sha256 == receipt.sha256
            ):
                await _inspect_file(criterion, check, artifact, version, reads)
    progress = sum(check.passed for check in checks)
    return GoalEvidence(
        all_passed=bool(checks) and progress == len(checks), progress=progress, checks=checks
    )
