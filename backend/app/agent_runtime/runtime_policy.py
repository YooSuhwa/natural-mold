"""Strict, versioned agent runtime-policy parsing and canonicalization.

This module defines the canonical persistence/API policy shape. Snapshotted
policies are enforced at the runtime graph's capability-specific build boundaries.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type RuntimePolicySource = Literal["legacy_compat", "stored"]
type RuntimePolicySnapshotSource = Literal["legacy_compat", "stored", "server_owned"]


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
    source: RuntimePolicySnapshotSource
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


def _fixed_server_policy() -> ResolvedRuntimePolicy:
    effective = RuntimePolicyV1(version=1)
    canonical_json = canonical_runtime_policy_json(effective)
    return ResolvedRuntimePolicy(
        effective=effective,
        source="server_owned",
        canonical_json=canonical_json,
        policy_hash=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    )


ASSISTANT_RUNTIME_POLICY: Final = _fixed_server_policy()
SKILL_BUILDER_RUNTIME_POLICY: Final = _fixed_server_policy()
LEGACY_RUNTIME_POLICY: Final = resolve_runtime_policy(None)


def validate_runtime_policy_snapshot(
    policy_json: Mapping[str, JsonValue] | None,
    version: int | None,
    policy_hash: str | None,
    source: str | None,
) -> ResolvedRuntimePolicy | None:
    """Parse a persisted conversation policy tuple or reject it without reflecting data."""
    if policy_json is None and version is None and policy_hash is None and source is None:
        return None
    if policy_json is None or version is None or policy_hash is None or source is None:
        raise RuntimePolicySnapshotError
    if type(version) is not int or version != 1:
        raise RuntimePolicySnapshotError
    match source:
        case "legacy_compat":
            snapshot_source: RuntimePolicySnapshotSource = "legacy_compat"
        case "stored":
            snapshot_source = "stored"
        case "server_owned":
            snapshot_source = "server_owned"
        case _:
            raise RuntimePolicySnapshotError
    try:
        effective = RuntimePolicyV1.model_validate(policy_json)
    except (TypeError, ValueError):
        raise RuntimePolicySnapshotError from None
    if dict(policy_json) != runtime_policy_to_json(effective):
        raise RuntimePolicySnapshotError
    canonical_json = canonical_runtime_policy_json(effective)
    computed_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    if policy_hash != computed_hash:
        raise RuntimePolicySnapshotError
    if (
        snapshot_source in {"legacy_compat", "server_owned"}
        and computed_hash != LEGACY_RUNTIME_POLICY.policy_hash
    ):
        raise RuntimePolicySnapshotError
    return ResolvedRuntimePolicy(
        effective=effective,
        source=snapshot_source,
        canonical_json=canonical_json,
        policy_hash=computed_hash,
    )


class RuntimePolicySnapshotError(Exception):
    """A stored runtime-policy tuple is incomplete, malformed, or internally inconsistent."""

    code = "RUNTIME_POLICY_SNAPSHOT_INVALID"

    def __str__(self) -> str:
        return self.code
