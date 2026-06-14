from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any

from app.marketplace.secret_scan import scan_package
from app.schemas.skill_builder import SkillDraftFile
from app.skills.compatibility import check_portable_compatibility
from app.skills.inspector import SkillMetadataError, parse_skill_md
from app.skills.package_builder import normalize_draft_path

TRIGGER_WORDS = ("use when", "사용", "when", "whenever")
SCAFFOLDING_RE = re.compile(
    r"(?i)(<!--|complete and informative|replace with|todo:|\[describe|\[replace)"
)
NETWORK_RE = re.compile(r"(?i)(\bcurl\b|https?://)")
ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SCRIPT_EXTENSIONS = frozenset({".py", ".js", ".cjs", ".mjs", ".sh"})


def validate_draft_package(
    *,
    files: Sequence[SkillDraftFile],
    credential_requirements: Sequence[Mapping[str, Any]] | None = None,
    execution_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    normalized_files = _normalize_files(files, issues)
    by_path = {file.path: file for file in normalized_files}
    skill_file = by_path.get("SKILL.md")
    parsed: dict[str, Any] | None = None
    if skill_file is None:
        _add_issue(issues, "SKILL_MD_MISSING", "error", "SKILL.md is required.", "SKILL.md")
    else:
        try:
            parsed = parse_skill_md(skill_file.content, require_metadata=True)
        except SkillMetadataError as exc:
            _add_issue(issues, "SKILL_MD_METADATA_INVALID", "error", str(exc), "SKILL.md")

    if parsed is not None:
        metadata = parsed["metadata"]
        body = str(parsed["body"] or "")
        _validate_description(metadata, issues)
        _validate_body(body, issues)
        _validate_references(body, by_path, issues)
        _validate_scripts(body, by_path, issues)
        _validate_network(body, by_path, execution_profile or {}, issues)

    _validate_credential_requirements(credential_requirements or (), issues)
    _scan_secrets(normalized_files, issues)
    compatibility_result = check_portable_compatibility(normalized_files)
    error_count = _count(issues, "error") + compatibility_result["error_count"]
    warning_count = _count(issues, "warning") + compatibility_result["warning_count"]
    info_count = _count(issues, "info") + compatibility_result["info_count"]
    return {
        "valid": error_count == 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "issues": issues,
        "compatibility_result": compatibility_result,
    }


def _normalize_files(
    files: Sequence[SkillDraftFile],
    issues: list[dict[str, Any]],
) -> list[SkillDraftFile]:
    normalized: list[SkillDraftFile] = []
    for draft_file in files:
        try:
            path = normalize_draft_path(draft_file.path)
        except ValueError as exc:
            _add_issue(issues, "INVALID_PATH", "error", str(exc), draft_file.path)
            continue
        normalized.append(draft_file.model_copy(update={"path": path}))
    return normalized


def _validate_description(metadata: Mapping[str, Any], issues: list[dict[str, Any]]) -> None:
    description = str(metadata.get("description") or "").strip()
    lowered = description.lower()
    if len(description) < 80 or not any(word in lowered for word in TRIGGER_WORDS):
        _add_issue(
            issues,
            "WEAK_TRIGGER_DESCRIPTION",
            "warning",
            "Description should state concrete trigger conditions.",
            "SKILL.md",
        )


def _validate_body(body: str, issues: list[dict[str, Any]]) -> None:
    if SCAFFOLDING_RE.search(body):
        _add_issue(
            issues,
            "SCAFFOLDING_MARKER",
            "warning",
            "SKILL.md still contains scaffold or placeholder text.",
            "SKILL.md",
        )
    if len(body.splitlines()) > 500:
        _add_issue(
            issues,
            "SKILL_MD_TOO_LONG",
            "warning",
            "SKILL.md is over 500 lines; move details into references/.",
            "SKILL.md",
        )


def _validate_references(
    body: str,
    by_path: Mapping[str, SkillDraftFile],
    issues: list[dict[str, Any]],
) -> None:
    references = [path for path in by_path if path.startswith("references/")]
    if references and not any(path in body or "references/" in body for path in references):
        _add_issue(
            issues,
            "UNMENTIONED_REFERENCES",
            "warning",
            "references/ files should be mentioned from SKILL.md.",
            "references/",
        )


def _validate_scripts(
    body: str,
    by_path: Mapping[str, SkillDraftFile],
    issues: list[dict[str, Any]],
) -> None:
    scripts = [path for path in by_path if path.startswith("scripts/")]
    for script_path in scripts:
        if Path(script_path).suffix not in SCRIPT_EXTENSIONS:
            _add_issue(
                issues,
                "UNSUPPORTED_SCRIPT_EXTENSION",
                "error",
                "Script files must use a supported executable extension.",
                script_path,
            )
    if scripts and not any(path in body or "scripts/" in body for path in scripts):
        _add_issue(
            issues,
            "SCRIPT_USAGE_UNDOCUMENTED",
            "warning",
            "SKILL.md should explain when scripts should run.",
            "scripts/",
        )


def _validate_network(
    body: str,
    by_path: Mapping[str, SkillDraftFile],
    execution_profile: Mapping[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    uses_network = NETWORK_RE.search(body) is not None or any(
        file.path.startswith("scripts/") and NETWORK_RE.search(file.content)
        for file in by_path.values()
    )
    if uses_network and execution_profile.get("requires_network") is not True:
        _add_issue(
            issues,
            "NETWORK_PROFILE_MISSING",
            "warning",
            "Network usage should set execution_profile.requires_network=true.",
            "agents/moldy.yaml",
        )


def _validate_credential_requirements(
    requirements: Sequence[Mapping[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    if not requirements:
        return
    import_module("app.credentials.definitions")
    from app.credentials.registry import registry

    for index, requirement in enumerate(requirements):
        path = f"credential_requirements[{index}]"
        missing = [
            field
            for field in ("key", "definition_key", "required", "label")
            if field not in requirement
        ]
        if missing:
            _add_issue(
                issues,
                "CREDENTIAL_REQUIREMENT_MALFORMED",
                "error",
                f"Credential requirement is missing: {', '.join(missing)}.",
                path,
            )
            continue
        definition_key = str(requirement["definition_key"])
        definition = registry.get(definition_key)
        if definition is None:
            _add_issue(
                issues,
                "UNKNOWN_CREDENTIAL_DEFINITION",
                "error",
                f"Unknown credential definition_key: {definition_key}.",
                path,
            )
            continue
        definition_fields = {field.name for field in definition.properties}
        declared_fields = _string_set(requirement.get("fields"))
        unknown_fields = declared_fields - definition_fields
        if unknown_fields:
            _add_issue(
                issues,
                "UNKNOWN_CREDENTIAL_FIELD",
                "error",
                f"Unknown credential fields: {', '.join(sorted(unknown_fields))}.",
                path,
            )
        env_map = requirement.get("env_map")
        if env_map is not None:
            _validate_env_map(env_map, declared_fields, issues, path)


def _validate_env_map(
    env_map: object,
    declared_fields: set[str],
    issues: list[dict[str, Any]],
    path: str,
) -> None:
    if not isinstance(env_map, Mapping):
        _add_issue(
            issues,
            "CREDENTIAL_ENV_MAP_INVALID",
            "error",
            "env_map must be an object.",
            path,
        )
        return
    for raw_field, raw_env_var in env_map.items():
        field = str(raw_field)
        env_var = str(raw_env_var)
        if field not in declared_fields and env_var in declared_fields and ENV_VAR_RE.match(field):
            _add_issue(
                issues,
                "CREDENTIAL_ENV_MAP_REVERSED",
                "error",
                "env_map must be {credential_field_name: ENV_VAR_NAME}.",
                f"{path}.env_map",
            )
            continue
        if field not in declared_fields:
            _add_issue(
                issues,
                "CREDENTIAL_ENV_FIELD_UNDECLARED",
                "error",
                f"env_map field is not listed in fields: {field}.",
                f"{path}.env_map",
            )
            continue
        if not ENV_VAR_RE.match(env_var):
            _add_issue(
                issues,
                "CREDENTIAL_ENV_VAR_INVALID",
                "error",
                f"Invalid environment variable name: {env_var}.",
                f"{path}.env_map",
            )


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return set()
    return {str(item) for item in value}


def _scan_secrets(files: Sequence[SkillDraftFile], issues: list[dict[str, Any]]) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        for draft_file in files:
            target = root / draft_file.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(draft_file.content, encoding="utf-8")
        for finding in scan_package(root):
            _add_issue(
                issues,
                "SECRET_DETECTED",
                "error",
                f"Potential secret detected by {finding.kind} scanner.",
                finding.path,
            )


def _add_issue(
    issues: list[dict[str, Any]],
    code: str,
    severity: str,
    message: str,
    path: str | None = None,
) -> None:
    issues.append({"code": code, "severity": severity, "path": path, "message": message})


def _count(issues: Sequence[Mapping[str, Any]], severity: str) -> int:
    return sum(1 for issue in issues if issue.get("severity") == severity)
