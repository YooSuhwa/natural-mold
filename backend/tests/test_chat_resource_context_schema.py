from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.chat_resource_context import (
    ChatResourceContextRequest,
    ChatResourceReference,
)


def test_resource_reference_rejects_paths_urls_and_client_snapshots() -> None:
    with pytest.raises(ValidationError):
        ChatResourceReference.model_validate(
            {
                "kind": "file",
                "id": str(uuid.uuid4()),
                "label": "notes",
                "path": "/etc/passwd",
                "url": "https://example.test/private",
                "text": "client supplied snapshot",
            }
        )


def test_resource_reference_allows_version_only_for_artifact() -> None:
    with pytest.raises(ValidationError):
        ChatResourceReference(
            kind="skill",
            id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            label="skill",
        )


def test_resource_context_rejects_more_than_eight_references() -> None:
    references = [
        ChatResourceReference(kind="conversation", id=uuid.uuid4(), label=f"thread-{index}")
        for index in range(9)
    ]

    with pytest.raises(ValidationError):
        ChatResourceContextRequest(resources=references)


def test_resource_reference_requires_uuid_identifier() -> None:
    with pytest.raises(ValidationError):
        ChatResourceReference.model_validate(
            {"kind": "file", "id": "https://example.test/not-an-id", "label": "bad"}
        )
