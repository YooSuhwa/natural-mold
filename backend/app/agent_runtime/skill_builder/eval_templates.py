from __future__ import annotations

from dataclasses import dataclass

from app.schemas.skill_builder import JsonValue


@dataclass(frozen=True, slots=True)
class EvalTemplate:
    key: str
    label: str
    case_count: int
    expectations: tuple[str, ...]
    grader_focus: tuple[str, ...]


_STRUCTURED_SIGNALS = (
    "action item",
    "table",
    "csv",
    "json",
    "extract",
    "structured",
    "액션",
    "표",
    "추출",
    "구조화",
    "회의록",
)
_RESEARCH_SIGNALS = (
    "research",
    "source",
    "citation",
    "cite",
    "web",
    "검색",
    "조사",
    "출처",
    "근거",
    "자료",
)
_TEMPLATES = {
    "structured_extraction": EvalTemplate(
        key="structured_extraction",
        label="Structured extraction",
        case_count=3,
        expectations=("extract_required_fields", "preserve_source_facts", "format_output"),
        grader_focus=("schema_adherence", "field_completeness", "hallucination_resistance"),
    ),
    "research": EvalTemplate(
        key="research",
        label="Research",
        case_count=3,
        expectations=("answer_with_sources", "separate_claims_from_evidence"),
        grader_focus=("citation_quality", "source_relevance", "claim_support"),
    ),
    "general_task": EvalTemplate(
        key="general_task",
        label="General task",
        case_count=2,
        expectations=("follow_skill_instructions", "produce_useful_output"),
        grader_focus=("instruction_following", "output_quality"),
    ),
}


def select_eval_template(
    *,
    intent: str,
    draft_package: dict[str, JsonValue] | None = None,
) -> EvalTemplate:
    searchable = f"{intent}\n{_json_text(draft_package or {})}".casefold()
    if _has_signal(searchable, _RESEARCH_SIGNALS):
        return _TEMPLATES["research"]
    if _has_signal(searchable, _STRUCTURED_SIGNALS):
        return _TEMPLATES["structured_extraction"]
    return _TEMPLATES["general_task"]


def _has_signal(text: str, signals: tuple[str, ...]) -> bool:
    return any(signal.casefold() in text for signal in signals)


def _json_text(value: JsonValue) -> str:
    match value:
        case str():
            return value
        case int() | float() | bool() | None:
            return ""
        case list():
            return " ".join(_json_text(item) for item in value)
        case dict():
            return " ".join(_json_text(item) for item in value.values())
        case _:
            return ""
