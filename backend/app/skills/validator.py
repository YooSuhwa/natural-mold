from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping, Sequence
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
SCRIPT_EXTENSIONS = frozenset({".py", ".js", ".cjs", ".mjs", ".sh"})


def validate_draft_package(
    *,
    files: Sequence[SkillDraftFile],
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
