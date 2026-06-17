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


def test_unknown_credential_definition_key_is_error() -> None:
    result = validate_draft_package(
        files=[_file("SKILL.md", _skill_md())],
        credential_requirements=[
            {
                "key": "llm",
                "definition_key": "missing_provider",
                "required": True,
                "label": "LLM key",
                "fields": ["api_key"],
                "env_map": {"api_key": "OPENAI_API_KEY"},
            }
        ],
    )

    assert result["valid"] is False
    assert "UNKNOWN_CREDENTIAL_DEFINITION" in _codes(result)


def test_reversed_env_map_shape_is_error() -> None:
    result = validate_draft_package(
        files=[_file("SKILL.md", _skill_md())],
        credential_requirements=[
            {
                "key": "openai",
                "definition_key": "openai",
                "required": True,
                "label": "OpenAI key",
                "fields": ["api_key"],
                "env_map": {"OPENAI_API_KEY": "api_key"},
            }
        ],
    )

    assert "CREDENTIAL_ENV_MAP_REVERSED" in _codes(result)


def test_env_map_field_must_be_declared() -> None:
    result = validate_draft_package(
        files=[_file("SKILL.md", _skill_md())],
        credential_requirements=[
            {
                "key": "openai",
                "definition_key": "openai",
                "required": True,
                "label": "OpenAI key",
                "fields": ["organization"],
                "env_map": {"api_key": "OPENAI_API_KEY"},
            }
        ],
    )

    assert "CREDENTIAL_ENV_FIELD_UNDECLARED" in _codes(result)


def test_env_map_value_must_be_valid_env_var_name() -> None:
    result = validate_draft_package(
        files=[_file("SKILL.md", _skill_md())],
        credential_requirements=[
            {
                "key": "openai",
                "definition_key": "openai",
                "required": True,
                "label": "OpenAI key",
                "fields": ["api_key"],
                "env_map": {"api_key": "openai api key"},
            }
        ],
    )

    assert "CREDENTIAL_ENV_VAR_INVALID" in _codes(result)


def test_agents_moldy_yaml_credential_requirements_are_validated() -> None:
    result = validate_draft_package(
        files=[
            _file("SKILL.md", _skill_md()),
            _file(
                "agents/moldy.yaml",
                "credential_requirements:\n"
                "  - key: llm\n"
                "    definition_key: missing_provider\n"
                "    required: true\n"
                "    label: LLM key\n"
                "    fields: [api_key]\n"
                "    env_map:\n"
                "      api_key: OPENAI_API_KEY\n",
            ),
        ],
    )

    assert "UNKNOWN_CREDENTIAL_DEFINITION" in _codes(result)


def test_agents_moldy_yaml_execution_profile_allows_network_usage() -> None:
    result = validate_draft_package(
        files=[
            _file("SKILL.md", _skill_md(body="Use when fetching https://api.example.com.")),
            _file("scripts/run.py", "curl https://api.example.com\n"),
            _file("agents/moldy.yaml", "execution_profile:\n  requires_network: true\n"),
        ],
    )

    assert "NETWORK_PROFILE_MISSING" not in _codes(result)


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
