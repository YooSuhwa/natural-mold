"""Accept one protocol command and schedule the resulting durable work after commit."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata

from ag_ui.core import RunAgentInput
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import ValidationError
from app.models.conversation import Conversation
from app.services.ag_ui_commands import AgUiRunBinding, find_or_reserve
from app.services.ag_ui_start import accept_start, wake_queued_start

logger = logging.getLogger(__name__)


def _validate_version(version: str | None) -> None:
    if version is None or version == "1.0":
        return
    parts = re.fullmatch(r"([0-9]+)\.([0-9]+)", version)
    if parts is None:
        logger.warning("Unrecognized AG-UI version format; serving protocol 1.0")
    elif parts[1].lstrip("0") != "1":
        raise ValidationError("AG_UI_VERSION_UNSUPPORTED", "Unsupported AG-UI protocol major")
    elif parts[2].strip("0"):
        logger.warning("Consumer requested a newer AG-UI minor version; serving protocol 1.0")


def _request_digest(run_input: RunAgentInput) -> str:
    # Include explicit nulls: for example, a cancelled resume must omit payload.
    # Only the digest is persisted, never echoed history, forwardedProps or tool definitions.
    try:
        encoded = json.dumps(
            run_input.model_dump(mode="json", by_alias=True, exclude_unset=True),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("AG_UI_INVALID_INPUT", "Invalid AG-UI JSON input") from exc
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


async def submit_ag_ui_run(
    db: AsyncSession,
    *,
    request: Request,
    context: OrgContext,
    user: CurrentUser,
    conversation: Conversation,
    run_input: RunAgentInput,
) -> AgUiRunBinding:
    if run_input.thread_id != str(conversation.id):
        raise ValidationError("AG_UI_THREAD_MISMATCH", "The thread ID must match the conversation")
    if not 1 <= len(run_input.run_id) <= 200 or any(
        unicodedata.category(character) in {"Cc", "Cs"} for character in run_input.run_id
    ):
        raise ValidationError("AG_UI_INVALID_RUN_ID", "Invalid public run identifier")
    _validate_version(run_input.protocol_version)
    conversation_id = conversation.id
    try:
        admission = await find_or_reserve(
            db, context, conversation_id, run_input.run_id, _request_digest(run_input)
        )
        binding = admission.existing
        if binding is None:
            if run_input.resume:
                from app.services.ag_ui_accept_resume import accept_resume

                binding = await accept_resume(
                    db,
                    context=context,
                    user=user,
                    conversation=conversation,
                    request=request,
                    entries=run_input.resume,
                    admission=admission,
                )
            else:
                binding = await accept_start(
                    db=db,
                    context=context,
                    user=user,
                    conversation=conversation,
                    request=request,
                    run_input=run_input,
                    admission=admission,
                )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    if admission.existing is None:
        # Both paths are durable. A failed local wake leaves work for the existing
        # recovery sweep, including a server restart immediately after the commit.
        try:
            if binding.input_id is not None:
                await wake_queued_start(request, conversation_id)
            elif binding.run_id is not None:
                from app.services.ag_ui_deferred_delivery import dispatch_ag_ui_run

                await dispatch_ag_ui_run(binding.run_id)
        except Exception as exc:
            # The request is committed. Response attachment can now observe execution
            # by another process. Avoid exposing runtime payloads from exception strings.
            logger.warning(
                "Committed AG-UI command awaits worker recovery (%s)",
                type(exc).__name__,
                extra={
                    "conversation_id": str(conversation_id),
                },
            )
    return binding
