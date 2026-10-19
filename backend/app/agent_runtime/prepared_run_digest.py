"""Canonical non-secret SHA-256 digest of a prepared run (parent Task 10).

The digest is persisted as ``runtime_materialization_sha256`` alongside
``runtime_materialized_at``. It covers identity, versions, snapshot digests,
participant paths, tool access classes, checkpoint lineage and secret
*reference* IDs — never secret values, configuration or snapshot content.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from app.agent_runtime.runtime_config import TOOL_ACCESS_CLASSES, PreparedRunBinding

DIGEST_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PreparedRunDigestMaterial:
    """Non-secret digest inputs. ``tool_access`` keys should be participant-qualified
    (``"<participant path>/<tool name>"``) when classes can differ per participant."""

    binding: PreparedRunBinding
    versions: Mapping[str, str]
    snapshot_digests: Mapping[str, str]
    tool_access: Mapping[str, str]
    checkpoint_lineage: tuple[str, str, str | None]
    secret_reference_ids: tuple[str, ...]
    descriptor_ids: frozenset[str] = frozenset()


def _strings(field_name: str, values: Mapping[str, str]) -> dict[str, str]:
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
        raise ValueError(f"digest_field_not_string:{field_name}")
    return {key: values[key] for key in sorted(values)}


def _lineage(lineage: tuple[str, str, str | None]) -> list[str | None]:
    if (
        len(lineage) != 3
        or not all(isinstance(item, str) for item in lineage[:2])
        or not (lineage[2] is None or isinstance(lineage[2], str))
    ):
        raise ValueError("digest_field_not_string:checkpoint_lineage")
    return list(lineage)


def canonical_digest_bytes(material: PreparedRunDigestMaterial) -> bytes:
    """Return the canonical JSON bytes hashed by :func:`compute_prepared_run_digest`."""

    unknown = sorted(str(v) for v in set(material.tool_access.values()) - TOOL_ACCESS_CLASSES)
    if unknown:
        raise ValueError(f"tool_access_class_unknown:{','.join(unknown)}")
    binding = material.binding
    document = {
        "schema_version": DIGEST_SCHEMA_VERSION,
        "binding": {
            "tenant_id": str(binding.tenant_id),
            "org_id": str(binding.org_id),
            "user_id": str(binding.user_id),
            "agent_id": str(binding.agent_id),
            "conversation_id": str(binding.conversation_id),
            "run_id": str(binding.run_id),
            "thread_id": binding.thread_id,
            "participant_paths": sorted(binding.participant_paths),
        },
        "versions": _strings("versions", material.versions),
        "snapshot_digests": _strings("snapshot_digests", material.snapshot_digests),
        "tool_access": _strings("tool_access", material.tool_access),
        "checkpoint_lineage": _lineage(material.checkpoint_lineage),
        "secret_reference_ids": sorted(set(material.secret_reference_ids)),
        "descriptor_ids": sorted(material.descriptor_ids),
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_prepared_run_digest(material: PreparedRunDigestMaterial) -> str:
    return hashlib.sha256(canonical_digest_bytes(material)).hexdigest()
