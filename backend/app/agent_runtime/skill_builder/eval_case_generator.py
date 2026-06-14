from __future__ import annotations

from app.agent_runtime.skill_builder.eval_schema import SkillEvalCase
from app.agent_runtime.skill_builder.eval_templates import EvalTemplate


def generate_eval_cases(
    *,
    intent: str,
    template: EvalTemplate,
) -> list[SkillEvalCase]:
    match template.key:
        case "structured_extraction":
            return _structured_cases(intent, template)
        case "research":
            return _research_cases(intent, template)
        case _:
            return _general_cases(intent, template)


def _structured_cases(intent: str, template: EvalTemplate) -> list[SkillEvalCase]:
    expected = {
        "format": "structured",
        "required_fields": ["task", "owner", "deadline"],
    }
    inputs = (
        "다음 회의록에서 액션 아이템, 담당자, 마감일을 추출하세요.",
        "긴 노트에서 결정사항과 후속 작업을 구분해 표로 정리하세요.",
        "누락된 담당자나 기한이 있으면 빈 값으로 표시하고 추측하지 마세요.",
    )
    return [_case(intent, template, item, expected, ["structured"]) for item in inputs]


def _research_cases(intent: str, template: EvalTemplate) -> list[SkillEvalCase]:
    expected = {
        "format": "answer_with_citations",
        "minimum_sources": 2,
    }
    inputs = (
        "주어진 주제의 핵심 주장 3개를 출처와 함께 요약하세요.",
        "서로 다른 출처의 관점을 비교하고 근거 링크를 함께 제시하세요.",
        "불확실한 내용은 추정하지 말고 추가 확인이 필요하다고 표시하세요.",
    )
    return [_case(intent, template, item, expected, ["research", "citation"]) for item in inputs]


def _general_cases(intent: str, template: EvalTemplate) -> list[SkillEvalCase]:
    expected = {"format": "useful_answer"}
    inputs = (
        "사용자의 원래 의도를 보존하면서 결과를 개선하세요.",
        "모호한 입력에는 필요한 가정을 짧게 밝히고 실행 가능한 결과를 내세요.",
    )
    return [_case(intent, template, item, expected, ["general"]) for item in inputs]


def _case(
    intent: str,
    template: EvalTemplate,
    input_text: str,
    expected: dict[str, str | int | list[str]],
    tags: list[str],
) -> SkillEvalCase:
    return SkillEvalCase(
        input={
            "intent": intent,
            "prompt": input_text,
        },
        expected=expected,
        tags=tags,
        metadata={
            "template_key": template.key,
            "intent": intent,
        },
    )
