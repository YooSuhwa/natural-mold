from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

from app.schemas.skill_builder import SkillDraftFile
from app.skills.inspector import SkillMetadataError, parse_skill_md

CompatibilityStatus = Literal["pass", "warning", "error"]

TARGETS = ("openai_codex", "claude_code", "vercel_agent_skills")
MOLDY_ONLY_FRONTMATTER = frozenset(
    {
        "credential_requirements",
        "execution_profile",
        "eval_policy",
        "skill_builder_session_id",
        "moldy",
        "rollback",
        "revision_history",
    }
)
LOCAL_REFERENCE_RE = re.compile(
    r"(?i)(/Users/|/home/|/var/|[A-Z]:\\|localhost|127\.0\.0\.1|data/skills/|backend/|\\.env|/api/)"
)
CHANGELOG_RE = re.compile(r"(?i)(^|\n)#{1,3}\s*(change\s*log|changelog|변경\s*이력|eval results?)")


def check_portable_compatibility(files: Sequence[SkillDraftFile]) -> dict[str, Any]:
    by_path = {_normalize_path(file.path): file for file in files}
    targets = {
        target: {"status": "pass", "issues": []}
        for target in TARGETS
    }
    skill_file = by_path.get("SKILL.md")
    if skill_file is None:
        _add_all(targets, "SKILL_MD_MISSING", "error", "SKILL.md is required.")
        return _finalize(targets)

    try:
        parsed = parse_skill_md(skill_file.content, require_metadata=True)
    except SkillMetadataError as exc:
        _add_all(targets, "SKILL_MD_METADATA_INVALID", "error", str(exc), "SKILL.md")
        return _finalize(targets)

    metadata = parsed["metadata"]
    body = parsed["body"]
    name = str(metadata.get("name") or "").strip()
    moldy_keys = sorted(set(metadata) & MOLDY_ONLY_FRONTMATTER)
    if moldy_keys:
        _add_all(
            targets,
            "MOLDY_ONLY_FRONTMATTER",
            "error",
            f"Move Moldy-only frontmatter keys out of SKILL.md: {', '.join(moldy_keys)}.",
            "SKILL.md",
        )

    openai_metadata = by_path.get("agents/openai.yaml")
    if openai_metadata is None:
        _add(
            targets,
            "openai_codex",
            "OPENAI_METADATA_MISSING",
            "warning",
            "Generated portable skills should include agents/openai.yaml.",
            "agents/openai.yaml",
        )
    elif name and f"${name}" not in openai_metadata.content:
        _add(
            targets,
            "openai_codex",
            "OPENAI_DEFAULT_PROMPT_MISSING_SKILL",
            "warning",
            "agents/openai.yaml default_prompt should mention the skill handle.",
            "agents/openai.yaml",
        )

    if LOCAL_REFERENCE_RE.search(body):
        for target in ("claude_code", "vercel_agent_skills"):
            _add(
                targets,
                target,
                "LOCAL_PATH_REFERENCE",
                "warning",
                "SKILL.md references local paths, localhost, environment files, "
                "or Moldy API routes.",
                "SKILL.md",
            )

    if CHANGELOG_RE.search(body):
        _add_all(
            targets,
            "CHANGELOG_IN_SKILL_MD",
            "warning",
            "Keep changelogs, evaluation summaries, and compatibility reports outside SKILL.md.",
            "SKILL.md",
        )

    if any(path.startswith("evals/") for path in by_path):
        _add_all(
            targets,
            "EVALS_PRESENT",
            "info",
            "evals/ can support Moldy quality checks but is excluded from default export.",
            "evals",
        )

    return _finalize(targets)


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("/")


def _add_all(
    targets: dict[str, dict[str, Any]],
    code: str,
    severity: str,
    message: str,
    path: str | None = None,
) -> None:
    for target in targets:
        _add(targets, target, code, severity, message, path)


def _add(
    targets: dict[str, dict[str, Any]],
    target: str,
    code: str,
    severity: str,
    message: str,
    path: str | None = None,
) -> None:
    targets[target]["issues"].append(
        {"code": code, "severity": severity, "path": path, "message": message}
    )


def _finalize(targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    error_count = 0
    warning_count = 0
    info_count = 0
    for target in targets.values():
        severities = [issue["severity"] for issue in target["issues"]]
        error_count += severities.count("error")
        warning_count += severities.count("warning")
        info_count += severities.count("info")
        target["status"] = _status_for(severities)
    return {
        "status": _status_for(
            ["error"] * error_count + ["warning"] * warning_count + ["info"] * info_count
        ),
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "targets": targets,
    }


def _status_for(severities: Sequence[str]) -> CompatibilityStatus:
    if "error" in severities:
        return "error"
    if "warning" in severities:
        return "warning"
    return "pass"
