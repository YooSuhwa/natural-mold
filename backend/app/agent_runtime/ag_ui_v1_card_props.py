"""Public props for chat widgets and skill-builder cards in an authorized run."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from app.agent_runtime.skill_validation_projection import SKILL_VALIDATION_TOOL_NAMES
from app.skills.compatibility import TARGETS

# The widget fields mirror frontend/src/lib/chat/data-ui-registry.ts.
type _Label = Annotated[str, Field(max_length=512)]
type _Text = Annotated[str, Field(max_length=8192)]
type _Id = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_.:@-]+$")]
type _Count = Annotated[int, Field(ge=0, le=2**53 - 1)]
type _Cell = str | bool | int | float | None


def _safe_number(value: int | float) -> int | float:
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        raise ValueError("Integer cannot be represented exactly by the chat client")
    return value


type _Number = Annotated[int | float, AfterValidator(_safe_number)]


class _PublicProps(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)


type _AttachmentId = Annotated[str, Field(min_length=1, max_length=256)]


class _Attachments(_PublicProps):
    message_id: _AttachmentId | None = None
    run_id: _AttachmentId | None = None
    tool_call_id: _AttachmentId | None = None

    @field_validator("message_id", "run_id", "tool_call_id")
    @classmethod
    def printable_id(cls, value: str | None) -> str | None:
        # Provider IDs are opaque. Preserve printable punctuation and Unicode exactly.
        if value is not None and (not value.isprintable() or not value.strip()):
            raise ValueError("Invalid card attachment identifier")
        return value


class _Note(_PublicProps):
    text: _Text


class _Column(_PublicProps):
    key: Annotated[str, Field(min_length=1, max_length=128)]
    header: _Label

    @field_validator("key")
    @classmethod
    def safe_key(cls, key: str) -> str:
        if not key.isprintable() or set(key.split(".")) & {"__proto__", "prototype", "constructor"}:
            raise ValueError("Invalid table column key")
        return key


def _public_cell(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= 1024
    if isinstance(value, int):
        return abs(value) <= 2**53 - 1
    return isinstance(value, float) and math.isfinite(value)


class _Table(_PublicProps):
    columns: Annotated[list[_Column], Field(max_length=64)]
    rows: Annotated[list[dict[str, _Cell]], Field(max_length=200)]
    title: _Label | None = None
    searchable: bool | None = None

    @field_validator("rows", mode="before")
    @classmethod
    def declared_cells(cls, rows: object, info: ValidationInfo) -> list[dict[str, Any]]:
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError("Invalid table rows")
        keys = [column.key for column in info.data.get("columns", [])]
        result = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid table row")
            result.append({key: row[key] for key in keys if key in row and _public_cell(row[key])})
        return result


class _Point(_PublicProps):
    label: _Label
    value: _Number


class _Chart(_PublicProps):
    chart_type: Literal["line", "bar"] = Field(alias="chartType")
    series: Annotated[list[_Point], Field(max_length=256)]
    title: _Label | None = None
    x_label: _Label | None = Field(default=None, alias="xLabel")
    y_label: _Label | None = Field(default=None, alias="yLabel")


class _Stat(_PublicProps):
    label: _Label
    value: _Label | _Number
    delta: _Number | None = None
    unit: _Label | None = None


class _Stats(_PublicProps):
    items: Annotated[list[_Stat], Field(max_length=100)]


class _Terminal(_PublicProps):
    lines: _Text | Annotated[list[_Text], Field(max_length=512)]
    exit_code: _Number | None = Field(default=None, alias="exitCode")
    command: _Label | None = None

    @field_validator("lines")
    @classmethod
    def bounded_output(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list) and sum(map(len, value)) > 8192:
            raise ValueError("Terminal output is too long")
        return value


_WIDGETS: dict[str, type[_PublicProps]] = {
    "demo_note": _Note,
    "data_table": _Table,
    "chart": _Chart,
    "stats": _Stats,
    "terminal": _Terminal,
}


def public_ui_data(payload: Mapping[str, object]) -> dict[str, Any] | None:
    """Keep the fields rendered by the matching widget and its attachment identifiers."""
    version = payload.get("schema_version", 1)
    kind = payload.get("type")
    if (
        type(version) is not int
        or version != 1
        or not isinstance(kind, str)
        or kind not in _WIDGETS
    ):
        return None
    try:
        props = _WIDGETS[kind].model_validate(payload.get("props"))
        attachments = _Attachments.model_validate(payload)
    except ValidationError:
        return None
    return {
        "schema_version": 1,
        "type": kind,
        "props": props.model_dump(by_alias=True, exclude_none=True),
        **attachments.model_dump(exclude_none=True),
    }


def _relative_path(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 512 or not value.isprintable():
        return None
    if value.startswith(("~", "/")) or "\\" in value or ":" in value or value != value.strip():
        return None
    # Validation issues also point to directories such as "scripts/".
    if any(part in {"", ".", ".."} for part in value.removesuffix("/").split("/")):
        return None
    return value


class _DraftFile(_PublicProps):
    path: str
    size: _Count

    @field_validator("path")
    @classmethod
    def relative_file(cls, value: str) -> str:
        if _relative_path(value) is None or value.endswith("/"):
            raise ValueError("Invalid package file")
        return value


class _Draft(_PublicProps):
    session_id: _Id
    mode: Literal["create", "improve"]
    slug: _Label | None = None
    file_count: _Count
    files: list[_DraftFile]
    changed_count: _Count
    credential_requirement_count: _Count = 0

    @field_validator("files", mode="before")
    @classmethod
    def public_files(cls, items: object) -> list[_DraftFile]:
        if not isinstance(items, list):
            raise ValueError("Invalid file list")
        files = []
        for item in items[:100]:
            try:
                files.append(_DraftFile.model_validate(item))
            except ValidationError:
                continue
        return files

    @field_validator("slug")
    @classmethod
    def display_name(cls, value: str | None) -> str | None:
        if value and value.isprintable() and not any(char in value for char in ("/", "\\")):
            return value
        return None


def public_skill_draft(payload: Mapping[str, object]) -> dict[str, Any] | None:
    try:
        return _Draft.model_validate(payload).model_dump()
    except ValidationError:
        return None


# Parser exceptions can quote YAML, credential values, filesystem paths or an entire
# invalid package. Keep public explanations independent of the raw diagnostic text.
_ISSUE_MESSAGES = {
    "SKILL_MD_MISSING": "SKILL.md is required.",
    "SKILL_MD_METADATA_INVALID": "Check the name, description and frontmatter syntax in SKILL.md.",
    "INVALID_PATH": "Use a package-relative path.",
    "WEAK_TRIGGER_DESCRIPTION": "Describe when to use the skill.",
    "SCAFFOLDING_MARKER": "Remove placeholder text from SKILL.md.",
    "SKILL_MD_TOO_LONG": "Move details from SKILL.md into references/.",
    "UNMENTIONED_REFERENCES": "Mention the reference files in SKILL.md.",
    "UNSUPPORTED_SCRIPT_EXTENSION": "Use a supported executable extension for the script.",
    "SCRIPT_USAGE_UNDOCUMENTED": "Explain when to run the scripts in SKILL.md.",
    "NETWORK_PROFILE_MISSING": "Declare network access in the execution profile.",
    "NETWORK_DOMAINS_INVALID": "Specify valid domains in the execution profile.",
    "NETWORK_DOMAINS_MISSING": "List the domains that the skill needs to reach.",
    "SECRET_DETECTED": "Remove the potential secret from the package.",
    "MOLDY_ONLY_FRONTMATTER": "Move Moldy-only metadata out of SKILL.md.",
    "OPENAI_METADATA_MISSING": "Include agents/openai.yaml.",
    "OPENAI_DEFAULT_PROMPT_MISSING_SKILL": "Mention the skill handle in the default prompt.",
    "LOCAL_PATH_REFERENCE": "Remove local paths, environment files and API routes from SKILL.md.",
    "CHANGELOG_IN_SKILL_MD": "Keep changelogs and evaluation reports outside SKILL.md.",
    "EVALS_PRESENT": "Evaluation files are excluded from the default export.",
    "MOLDY_METADATA_INVALID": "Check the structure and YAML syntax in agents/moldy.yaml.",
    "CREDENTIAL_REQUIREMENT_MALFORMED": "Check the required credential configuration fields.",
    "UNKNOWN_CREDENTIAL_DEFINITION": "Select a registered credential definition.",
    "UNKNOWN_CREDENTIAL_FIELD": "Use a field supported by the credential definition.",
    "CREDENTIAL_ENV_MAP_INVALID": "Provide an object for the credential environment mapping.",
    "CREDENTIAL_ENV_MAP_REVERSED": "Map each credential field to its environment variable name.",
    "CREDENTIAL_ENV_FIELD_UNDECLARED": "Declare the credential field used in the mapping.",
    "CREDENTIAL_ENV_VAR_INVALID": "Use a valid environment variable name.",
    "DRAFT_PACKAGE_MISSING": "Provide a draft package.",
    "DRAFT_PACKAGE_INVALID": "Check the draft package structure.",
    "EVALS_JSON_INVALID": "Check the evaluation file structure.",
}


def _public_issues(items: object) -> list[dict[str, str]] | None:
    if not isinstance(items, list):
        return None
    issues = []
    for item in items[:100]:
        if not isinstance(item, dict) or item.get("severity") not in ("error", "warning", "info"):
            continue
        code = item.get("code")
        if not isinstance(code, str) or code not in _ISSUE_MESSAGES:
            continue
        issue = {"code": code, "severity": item["severity"], "message": _ISSUE_MESSAGES[code]}
        path = _relative_path(item.get("path"))
        if path and code != "INVALID_PATH":
            issue["path"] = path
        issues.append(issue)
    return issues


class _Counts(_PublicProps):
    error_count: _Count | None = None
    warning_count: _Count | None = None
    info_count: _Count | None = None


def _public_compatibility(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("targets"), dict):
        return None
    targets = {}
    for name in TARGETS:
        raw = value["targets"].get(name)
        if not isinstance(raw, dict) or raw.get("status") not in ("pass", "error", "warning"):
            continue
        issues = _public_issues(raw.get("issues"))
        if issues is not None:
            targets[name] = {"status": raw["status"], "issues": issues}
    result = _Counts.model_validate(value).model_dump(exclude_none=True)
    if value.get("status") in ("pass", "error", "warning"):
        result["status"] = value["status"]
    return {**result, "targets": targets}


class _Validation(_Counts):
    valid: bool


class _ValidationSession(_PublicProps):
    session_id: _Id | None = None


def public_skill_validation(payload: Mapping[str, object]) -> dict[str, Any] | None:
    tool = payload.get("tool_name")
    raw = payload.get("validation_result")
    if (
        not isinstance(tool, str)
        or tool not in SKILL_VALIDATION_TOOL_NAMES
        or not isinstance(raw, dict)
    ):
        return None
    issues = _public_issues(raw.get("issues"))
    if issues is None:
        return None
    try:
        result = _Validation.model_validate(raw).model_dump(exclude_none=True)
        session = _ValidationSession.model_validate(payload).model_dump(exclude_none=True)
        compatibility = _public_compatibility(raw.get("compatibility_result"))
    except ValidationError:
        return None
    result["issues"] = issues
    if compatibility is not None:
        result["compatibility_result"] = compatibility
    return {"tool_name": tool, **session, "validation_result": result}
