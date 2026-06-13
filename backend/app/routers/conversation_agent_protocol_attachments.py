from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any


def attachment_ids_from_protocol_input(input_payload: dict[str, Any] | None) -> list[uuid.UUID]:
    if not isinstance(input_payload, Mapping):
        return []
    attachments = input_payload.get("attachments")
    if not isinstance(attachments, list):
        return []

    seen: set[uuid.UUID] = set()
    attachment_ids: list[uuid.UUID] = []
    for attachment in attachments:
        if not isinstance(attachment, Mapping):
            continue
        raw_id = attachment.get("id")
        if not isinstance(raw_id, str):
            continue
        try:
            attachment_id = uuid.UUID(raw_id)
        except ValueError:
            continue
        if attachment_id in seen:
            continue
        seen.add(attachment_id)
        attachment_ids.append(attachment_id)
    return attachment_ids


def input_without_protocol_attachments(input_payload: dict[str, Any]) -> dict[str, Any]:
    if "attachments" not in input_payload:
        return input_payload
    sanitized = dict(input_payload)
    del sanitized["attachments"]
    return sanitized
