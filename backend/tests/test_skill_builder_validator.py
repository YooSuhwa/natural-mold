from __future__ import annotations

from app.schemas.skill_builder import SkillDraftFile
from app.skills.validator import validate_draft_package


def _skill_md(
    *,
    name: str = "meeting-notes",
    description: str | None = "Use when summarizing meeting notes into action items.",
    body: str = "Use when summarizing meeting notes. See references/guide.md.",
) -> str:
    description_line = "" if description is None else f'description: "{description}"\n'
    return (
        "---\n"
        f"name: {name}\n"
        f"{description_line}"
        "---\n\n"
        f"{body}\n"
    )


def _file(path: str, content: str) -> SkillDraftFile:
    return SkillDraftFile(path=path, content=content)


def _codes(result: dict) -> set[str]:
    return {issue["code"] for issue in result["issues"]}


def test_missing_skill_md_is_error() -> None:
    result = validate_draft_package(files=[_file("references/guide.md", "Guide")])

    assert result["valid"] is False
    assert "SKILL_MD_MISSING" in _codes(result)


def test_missing_frontmatter_description_is_error() -> None:
    result = validate_draft_package(files=[_file("SKILL.md", _skill_md(description=None))])

    assert result["valid"] is False
    assert "SKILL_MD_METADATA_INVALID" in _codes(result)


def test_weak_trigger_description_is_warning() -> None:
    result = validate_draft_package(
        files=[_file("SKILL.md", _skill_md(description="Summarizes."))]
    )

    assert result["valid"] is True
    assert "WEAK_TRIGGER_DESCRIPTION" in _codes(result)


def test_scratch_html_comment_is_warning() -> None:
    result = validate_draft_package(
        files=[
            _file(
                "SKILL.md",
                _skill_md(body="<!-- Complete and informative instructions go here -->"),
            )
        ]
    )

    assert "SCAFFOLDING_MARKER" in _codes(result)


def test_references_without_skill_md_mention_is_warning() -> None:
    result = validate_draft_package(
        files=[
            _file("SKILL.md", _skill_md(body="Use when summarizing meeting notes.")),
            _file("references/guide.md", "Guide"),
        ]
    )

    assert "UNMENTIONED_REFERENCES" in _codes(result)


def test_secret_looking_content_is_error() -> None:
    result = validate_draft_package(
        files=[
            _file("SKILL.md", _skill_md()),
            _file("scripts/run.py", "OPENAI_API_KEY='sk-realrealrealrealrealreal'\n"),
        ]
    )

    assert result["valid"] is False
    assert "SECRET_DETECTED" in _codes(result)


def test_network_command_without_network_profile_is_warning() -> None:
    result = validate_draft_package(
        files=[
            _file("SKILL.md", _skill_md(body="Use when fetching https://api.example.com.")),
            _file("scripts/run.py", "curl https://api.example.com\n"),
        ],
        execution_profile={},
    )

    assert "NETWORK_PROFILE_MISSING" in _codes(result)


def test_compatibility_result_is_included() -> None:
    result = validate_draft_package(files=[_file("SKILL.md", _skill_md())])

    assert result["compatibility_result"]["status"] == "warning"
    assert "openai_codex" in result["compatibility_result"]["targets"]


def test_valid_portable_package_returns_valid_true() -> None:
    result = validate_draft_package(
        files=[
            _file("SKILL.md", _skill_md()),
            _file(
                "agents/openai.yaml",
                "interface:\n  default_prompt: \"$meeting-notes summarize this\"\n",
            ),
            _file("references/guide.md", "Guide"),
        ]
    )

    assert result["valid"] is True
    assert result["error_count"] == 0
