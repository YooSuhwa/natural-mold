from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.dependencies import get_db, get_org_context
from app.error_codes import file_not_found
from app.services.conversation_history_evidence import (
    EvidenceDigestChanged,
    EvidencePage,
    EvidenceUnavailable,
    HistoryEvidence,
    read_history_evidence,
)
from app.services.conversation_history_secrets import HistorySecretSourceError

router = APIRouter(tags=["conversations"])


@router.get(
    "/api/conversations/{conversation_id}/history-evidence/{checkpoint_id}",
    response_model=HistoryEvidence,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def get_history_evidence(
    conversation_id: UUID,
    checkpoint_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
    response: Response,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=32)] = 10,
    expected_sha256: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None,
    db: AsyncSession = Depends(get_db),
    context: OrgContext = Depends(get_org_context),
) -> HistoryEvidence:
    """Read display-safe evidence at an authenticated conversation's exact checkpoint."""
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return await read_history_evidence(
            db,
            conversation_id,
            context,
            checkpoint_id,
            EvidencePage(offset, limit, expected_sha256),
        )
    except EvidenceUnavailable as exc:
        raise file_not_found() from exc
    except EvidenceDigestChanged as exc:
        raise HTTPException(409, detail={"code": "history_evidence_digest_changed"}) from exc
    except HistorySecretSourceError as exc:
        raise HTTPException(503, detail={"code": "history_evidence_unavailable"}) from exc
