from __future__ import annotations

from app.schemas.skill_builder import SkillDraftFile
from app.skills.compatibility import check_portable_compatibility


def _skill_md(
    *,
    extra_frontmatter: str = "",
    body: str = "Use when you need to summarize meeting notes.\n",
) -> str:
    return (
        "---\n"
        "name: meeting-notes\n"
        'description: "Use when summarizing meeting notes into action items."\n'
        f"{extra_frontmatter}"
        "---\n\n"
        f"{body}"
    )


def _file(path: str, content: str) -> SkillDraftFile:
    return SkillDraftFile(path=path, content=content)


def _codes(result: dict, target: str) -> set[str]:
    return {issue["code"] for issue in result["targets"][target]["issues"]}


def test_valid_portable_package_passes_all_targets() -> None:
    result = check_portable_compatibility(
        [
            _file(
                "SKILL.md",
                _skill_md(body="Use when summarizing notes. See references/guide.md."),
            ),
            _file(
                "agents/openai.yaml",
                "interface:\n  default_prompt: \"$meeting-notes summarize this\"\n",
            ),
            _file("references/guide.md", "Guide\n"),
        ]
    )

    assert result["status"] == "pass"
    assert result["error_count"] == 0
    assert result["warning_count"] == 0


def test_moldy_only_frontmatter_is_compatibility_error() -> None:
    result = check_portable_compatibility(
        [
            _file(
                "SKILL.md",
                _skill_md(extra_frontmatter="credential_requirements: []\n"),
            ),
            _file(
                "agents/openai.yaml",
                "interface:\n  default_prompt: \"$meeting-notes summarize this\"\n",
            ),
        ]
    )

    assert result["status"] == "error"
    assert "MOLDY_ONLY_FRONTMATTER" in _codes(result, "openai_codex")


def test_missing_openai_metadata_is_warning() -> None:
    result = check_portable_compatibility([_file("SKILL.md", _skill_md())])

    assert result["status"] == "warning"
    assert "OPENAI_METADATA_MISSING" in _codes(result, "openai_codex")


def test_openai_default_prompt_should_reference_skill_name() -> None:
    result = check_portable_compatibility(
        [
            _file("SKILL.md", _skill_md()),
            _file("agents/openai.yaml", "interface:\n  default_prompt: \"summarize this\"\n"),
        ]
    )

    assert "OPENAI_DEFAULT_PROMPT_MISSING_SKILL" in _codes(result, "openai_codex")


def test_absolute_local_paths_are_portability_warnings() -> None:
    result = check_portable_compatibility(
        [
            _file(
                "SKILL.md",
                _skill_md(body="Use when reading /Users/alice/private/data/skills/demo."),
            ),
            _file(
                "agents/openai.yaml",
                "interface:\n  default_prompt: \"$meeting-notes summarize this\"\n",
            ),
        ]
    )

    assert "LOCAL_PATH_REFERENCE" in _codes(result, "claude_code")
    assert "LOCAL_PATH_REFERENCE" in _codes(result, "vercel_agent_skills")


def test_changelog_inside_skill_md_is_warning() -> None:
    result = check_portable_compatibility(
        [
            _file(
                "SKILL.md",
                _skill_md(body="Use when summarizing notes.\n\n## Changelog\n- Updated evals"),
            ),
            _file(
                "agents/openai.yaml",
                "interface:\n  default_prompt: \"$meeting-notes summarize this\"\n",
            ),
        ]
    )

    assert "CHANGELOG_IN_SKILL_MD" in _codes(result, "openai_codex")
    assert result["status"] == "warning"
