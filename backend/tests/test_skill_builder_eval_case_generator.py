from __future__ import annotations

from app.agent_runtime.skill_builder.eval_case_generator import generate_eval_cases
from app.agent_runtime.skill_builder.eval_templates import select_eval_template


def test_generate_eval_cases_for_structured_extraction() -> None:
    template = select_eval_template(
        intent="회의록에서 액션 아이템과 담당자를 표로 추출",
        draft_package=None,
    )

    cases = generate_eval_cases(
        intent="회의록에서 액션 아이템과 담당자를 표로 추출",
        template=template,
    )

    assert len(cases) == 3
    assert cases[0].metadata["template_key"] == "structured_extraction"
    assert cases[0].expected == {
        "format": "structured",
        "required_fields": ["task", "owner", "deadline"],
    }


def test_generate_eval_cases_for_research() -> None:
    template = select_eval_template(
        intent="출처가 있는 시장 조사 요약",
        draft_package=None,
    )

    cases = generate_eval_cases(
        intent="출처가 있는 시장 조사 요약",
        template=template,
    )

    assert len(cases) == 3
    assert cases[0].expected == {
        "format": "answer_with_citations",
        "minimum_sources": 2,
    }
    assert "citation" in cases[0].tags


def test_generate_eval_cases_for_general_task() -> None:
    template = select_eval_template(
        intent="이메일 초안을 자연스럽게 다듬기",
        draft_package=None,
    )

    cases = generate_eval_cases(
        intent="이메일 초안을 자연스럽게 다듬기",
        template=template,
    )

    assert len(cases) == 2
    assert cases[0].expected == {"format": "useful_answer"}
    assert cases[0].metadata["intent"] == "이메일 초안을 자연스럽게 다듬기"
