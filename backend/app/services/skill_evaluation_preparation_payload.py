from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from app.models.skill import Skill
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_file_adapter import (
    SkillEvaluationFileAdapterError,
    normalize_evaluation_file_payload,
)
from app.storage.paths import resolve_data_path

type JsonObject = dict[str, JsonValue]


def load_embedded_payload(skill: Skill) -> JsonObject | None:
    path = _embedded_evals_path(skill)
    if path is None or not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillEvaluationFileAdapterError(f"invalid evals/evals.json: {exc.msg}") from exc
    if isinstance(raw, list):
        raw = {"evals": raw}
    if not isinstance(raw, dict):
        raise SkillEvaluationFileAdapterError("evals/evals.json must be a JSON object")
    return normalize_evaluation_file_payload(raw)


def payload_hash(
    *,
    source_kind: str,
    evals: list[JsonObject],
    marketplace_item_id: uuid.UUID | None,
    marketplace_version_id: uuid.UUID | None,
) -> str:
    payload = {
        "source_kind": source_kind,
        "evals": evals,
        "marketplace_item_id": str(marketplace_item_id) if marketplace_item_id else None,
        "marketplace_version_id": str(marketplace_version_id) if marketplace_version_id else None,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def generation_strategy(
    *,
    source_kind: str,
    payload_hash_value: str,
    marketplace_item_id: uuid.UUID | None,
    marketplace_version_id: uuid.UUID | None,
    model_name: str | None,
) -> JsonObject:
    strategy: JsonObject = {
        "kind": "prepared_evaluation_set",
        "source_kind": source_kind,
        "payload_hash": payload_hash_value,
    }
    if marketplace_item_id is not None:
        strategy["marketplace_item_id"] = str(marketplace_item_id)
    if marketplace_version_id is not None:
        strategy["marketplace_version_id"] = str(marketplace_version_id)
    if model_name is not None:
        strategy["model_name"] = model_name
    return strategy


def evals_with_preparation_metadata(
    *,
    evals: list[JsonObject],
    payload_hash_value: str,
    source_kind: str,
) -> list[JsonObject]:
    prepared: list[JsonObject] = []
    for case in evals:
        metadata = case.get("metadata")
        normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        normalized_metadata["preparation_hash"] = payload_hash_value
        normalized_metadata["prepared_source_kind"] = source_kind
        prepared.append({**case, "metadata": normalized_metadata})
    return prepared


def evals_from_payload(payload: JsonObject) -> list[JsonObject]:
    raw = payload.get("evals")
    if not isinstance(raw, list):
        raise SkillEvaluationFileAdapterError("prepared payload missing evals")
    return [dict(item) for item in raw if isinstance(item, dict)]


def payload_name(payload: JsonObject, source_kind: str) -> str:
    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    if source_kind == "llm_generated":
        return "Generated smoke evaluation"
    return "Imported package evaluation"


def payload_description(payload: JsonObject) -> str | None:
    description = payload.get("description")
    if isinstance(description, str):
        stripped = description.strip()
        return stripped or None
    return None


def _embedded_evals_path(skill: Skill) -> Path | None:
    if not skill.storage_path:
        return None
    root = resolve_data_path(skill.storage_path)
    base = root.parent if root.is_file() else root
    return base / "evals" / "evals.json"
