"""Strict, versioned agent runtime-policy parsing and canonicalization.

This module defines persistence/API semantics only. Runtime graph behavior is
intentionally unchanged until the snapshotted policy is propagated by M72.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type RuntimePolicySource = Literal["legacy_compat", "stored"]


class FilesystemPolicyV1(BaseModel):
    """Filesystem capability selection persisted in RuntimePolicyV1."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: Literal["inspect", "artifact_write"] = "artifact_write"


class TodoPolicyV1(BaseModel):
    """Todo middleware selection persisted in RuntimePolicyV1."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enabled: bool = True


class SummarizationAutoPolicyV1(BaseModel):
    """Use Deep Agents' automatic summarization defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: Literal["auto"] = "auto"


class SummarizationPresetPolicyV1(BaseModel):
    """Use the sole named summarization preset supported by policy v1."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: Literal["preset"]
    preset: Literal["balanced_context_v1"]


type SummarizationPolicyV1 = Annotated[
    SummarizationAutoPolicyV1 | SummarizationPresetPolicyV1,
    Field(discriminator="mode"),
]


class RuntimePolicyV1(BaseModel):
    """Strict persisted runtime behavior policy, discriminated by version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    filesystem: FilesystemPolicyV1 = Field(default_factory=FilesystemPolicyV1)
    todo: TodoPolicyV1 = Field(default_factory=TodoPolicyV1)
    summarization: SummarizationPolicyV1 = Field(default_factory=SummarizationAutoPolicyV1)

    @field_validator("version", mode="before")
    @classmethod
    def _require_exact_integer_version(cls, value: JsonValue) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("version must be the integer 1")
        return value


@dataclass(frozen=True, slots=True)
class ResolvedRuntimePolicy:
    """Trusted effective policy plus immutable provenance and digest."""

    effective: RuntimePolicyV1
    source: RuntimePolicySource
    canonical_json: str
    policy_hash: str


def runtime_policy_to_json(policy: RuntimePolicyV1) -> JsonObject:
    """Return the exact JSON-compatible persistence shape for a trusted policy."""
    match policy.summarization:
        case SummarizationAutoPolicyV1():
            summarization: JsonObject = {"mode": "auto"}
        case SummarizationPresetPolicyV1(preset=preset):
            summarization = {"mode": "preset", "preset": preset}
    return {
        "version": policy.version,
        "filesystem": {"mode": policy.filesystem.mode},
        "todo": {"enabled": policy.todo.enabled},
        "summarization": summarization,
    }


def canonical_runtime_policy_json(policy: RuntimePolicyV1) -> str:
    """Serialize a policy as compact, sorted, Unicode-preserving JSON."""
    return json.dumps(
        runtime_policy_to_json(policy),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def resolve_runtime_policy(stored: Mapping[str, JsonValue] | None) -> ResolvedRuntimePolicy:
    """Strictly parse stored JSON or resolve NULL to the legacy-compatible defaults."""
    source: RuntimePolicySource = "legacy_compat" if stored is None else "stored"
    effective = (
        RuntimePolicyV1(version=1) if stored is None else RuntimePolicyV1.model_validate(stored)
    )
    canonical_json = canonical_runtime_policy_json(effective)
    return ResolvedRuntimePolicy(
        effective=effective,
        source=source,
        canonical_json=canonical_json,
        policy_hash=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    )
