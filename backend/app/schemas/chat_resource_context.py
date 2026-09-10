from __future__ import annotations

import uuid
from typing import Final, Literal, NotRequired, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

ResourceKind = Literal["file", "artifact", "skill", "conversation"]

MAX_RESOURCE_CONTEXT_ITEMS = 8
MAX_RESOURCE_CONTEXT_ITEM_BYTES = 32 * 1024
MAX_RESOURCE_CONTEXT_TOTAL_BYTES = 128 * 1024
PUBLIC_RESOURCE_CONTEXT_KEY: Final = "resource_context"
INTERNAL_RESOURCE_CONTEXT_KEY: Final = "_moldy_resource_context_v1"


class PublicChatResourceReference(TypedDict):
    kind: ResourceKind
    id: str
    version_id: NotRequired[str]
    label: NotRequired[str]
    message_id: NotRequired[str]
    quote: NotRequired[str]
    comment: NotRequired[str]
    message_role: NotRequired[Literal["user", "assistant"]]


class ChatResourceReference(BaseModel):
    """Strict client reference; display labels never participate in lookup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ResourceKind
    id: uuid.UUID
    version_id: uuid.UUID | None = None
    label: str | None = Field(default=None, min_length=1, max_length=255)
    message_id: str | None = Field(default=None, min_length=1, max_length=255)
    quote: str | None = Field(default=None, min_length=1, max_length=8000)
    comment: str | None = Field(default=None, max_length=2000)
    message_role: Literal["user", "assistant"] | None = None

    def to_public_reference(self) -> PublicChatResourceReference:
        public: PublicChatResourceReference = {"kind": self.kind, "id": str(self.id)}
        if self.version_id is not None:
            public["version_id"] = str(self.version_id)
        if self.label is not None:
            public["label"] = self.label
        if self.message_id is not None:
            public["message_id"] = self.message_id
        if self.quote is not None:
            public["quote"] = self.quote
        if self.comment is not None:
            public["comment"] = self.comment
        if self.message_role is not None:
            public["message_role"] = self.message_role
        return public

    @model_validator(mode="after")
    def version_is_artifact_only(self) -> Self:
        if self.version_id is not None and self.kind != "artifact":
            raise PydanticCustomError(
                "resource_context_version",
                "version_id is supported only for artifact references",
            )
        has_quote_fields = any(
            value is not None
            for value in (self.message_id, self.quote, self.comment, self.message_role)
        )
        if has_quote_fields and (
            self.kind != "conversation"
            or not self.message_id
            or not self.quote
            or not self.quote.strip()
        ):
            raise PydanticCustomError(
                "resource_context_quote",
                "A conversation quote requires message_id and quote",
            )
        return self


class ChatResourceContextRequest(BaseModel):
    """Validated public resource-context request carried by ``run.start``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resources: list[ChatResourceReference] = Field(
        min_length=1,
        max_length=MAX_RESOURCE_CONTEXT_ITEMS,
    )

    @model_validator(mode="after")
    def references_are_unique(self) -> Self:
        identities = {
            (item.kind, item.id, item.version_id, item.message_id, item.quote)
            for item in self.resources
        }
        if len(identities) != len(self.resources):
            raise PydanticCustomError(
                "resource_context_duplicate",
                "resource references must be unique",
            )
        return self


class FrozenChatResource(BaseModel):
    """Server-authorized immutable snapshot persisted with a queued input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ResourceKind
    id: uuid.UUID
    version_id: uuid.UUID | None = None
    label: str | None = None
    message_id: str | None = None
    quote: str | None = None
    comment: str | None = None
    message_role: Literal["user", "assistant"] | None = None
    resolved_label: str
    mime_type: str
    source_version: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str
    content_available: bool = True

    def to_public_reference(self) -> PublicChatResourceReference:
        return ChatResourceReference(
            kind=self.kind,
            id=self.id,
            version_id=self.version_id,
            label=self.resolved_label if self.quote else self.label,
            message_id=self.message_id,
            quote=self.quote,
            comment=self.comment,
            message_role=self.message_role,
        ).to_public_reference()


class FrozenChatResourceContext(BaseModel):
    """Versioned server snapshot collection stored in queue input JSON."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    resources: list[FrozenChatResource] = Field(
        min_length=1,
        max_length=MAX_RESOURCE_CONTEXT_ITEMS,
    )


__all__ = [
    "ChatResourceContextRequest",
    "ChatResourceReference",
    "FrozenChatResource",
    "FrozenChatResourceContext",
    "MAX_RESOURCE_CONTEXT_ITEM_BYTES",
    "MAX_RESOURCE_CONTEXT_ITEMS",
    "MAX_RESOURCE_CONTEXT_TOTAL_BYTES",
    "INTERNAL_RESOURCE_CONTEXT_KEY",
    "PUBLIC_RESOURCE_CONTEXT_KEY",
    "PublicChatResourceReference",
    "ResourceKind",
]
