from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from importlib import import_module
from typing import Any

import frontmatter
import yaml

from app.schemas.skill_builder import SkillDraftFile

ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def load_moldy_metadata(
    by_path: Mapping[str, SkillDraftFile],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    moldy_file = by_path.get("agents/moldy.yaml")
    if moldy_file is None:
        return {}, []
    return parse_moldy_metadata_content(moldy_file.content)


def parse_moldy_metadata_content(content: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        loaded = frontmatter.YAMLHandler().load(content)
    except yaml.YAMLError as exc:
        return {}, [
            _issue(
                "MOLDY_METADATA_INVALID",
                "error",
                f"agents/moldy.yaml is invalid YAML: {exc}.",
                "agents/moldy.yaml",
            )
        ]
    if not isinstance(loaded, dict):
        return {}, [
            _issue(
                "MOLDY_METADATA_INVALID",
                "error",
                "agents/moldy.yaml must contain an object.",
                "agents/moldy.yaml",
            )
        ]
    return loaded, []


def execution_profile_from_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    value = metadata.get("execution_profile")
    if isinstance(value, Mapping):
        return value
    return {}


def credential_requirements_from_metadata(
    metadata: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    value = metadata.get("credential_requirements")
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def validate_credential_requirements(
    requirements: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not requirements:
        return []
    import_module("app.credentials.definitions")
    from app.credentials.registry import registry

    issues: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements):
        path = f"credential_requirements[{index}]"
        missing = [
            field
            for field in ("key", "definition_key", "required", "label")
            if field not in requirement
        ]
        if missing:
            issues.append(
                _issue(
                    "CREDENTIAL_REQUIREMENT_MALFORMED",
                    "error",
                    f"Credential requirement is missing: {', '.join(missing)}.",
                    path,
                )
            )
            continue
        definition_key = str(requirement["definition_key"])
        definition = registry.get(definition_key)
        if definition is None:
            issues.append(
                _issue(
                    "UNKNOWN_CREDENTIAL_DEFINITION",
                    "error",
                    f"Unknown credential definition_key: {definition_key}.",
                    path,
                )
            )
            continue
        definition_fields = {field.name for field in definition.properties}
        declared_fields = _string_set(requirement.get("fields"))
        unknown_fields = declared_fields - definition_fields
        if unknown_fields:
            issues.append(
                _issue(
                    "UNKNOWN_CREDENTIAL_FIELD",
                    "error",
                    f"Unknown credential fields: {', '.join(sorted(unknown_fields))}.",
                    path,
                )
            )
        env_map = requirement.get("env_map")
        if env_map is not None:
            issues.extend(_validate_env_map(env_map, declared_fields, path))
    return issues


def _validate_env_map(
    env_map: object,
    declared_fields: set[str],
    path: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not isinstance(env_map, Mapping):
        return [_issue("CREDENTIAL_ENV_MAP_INVALID", "error", "env_map must be an object.", path)]
    for raw_field, raw_env_var in env_map.items():
        field = str(raw_field)
        env_var = str(raw_env_var)
        if field not in declared_fields and env_var in declared_fields and ENV_VAR_RE.match(field):
            issues.append(
                _issue(
                    "CREDENTIAL_ENV_MAP_REVERSED",
                    "error",
                    "env_map must be {credential_field_name: ENV_VAR_NAME}.",
                    f"{path}.env_map",
                )
            )
            continue
        if field not in declared_fields:
            issues.append(
                _issue(
                    "CREDENTIAL_ENV_FIELD_UNDECLARED",
                    "error",
                    f"env_map field is not listed in fields: {field}.",
                    f"{path}.env_map",
                )
            )
            continue
        if not ENV_VAR_RE.match(env_var):
            issues.append(
                _issue(
                    "CREDENTIAL_ENV_VAR_INVALID",
                    "error",
                    f"Invalid environment variable name: {env_var}.",
                    f"{path}.env_map",
                )
            )
    return issues


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return set()
    return {str(item) for item in value}


def _issue(
    code: str,
    severity: str,
    message: str,
    path: str | None = None,
) -> dict[str, Any]:
    return {"code": code, "severity": severity, "path": path, "message": message}
