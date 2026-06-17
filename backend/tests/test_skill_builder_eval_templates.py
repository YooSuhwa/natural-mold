from __future__ import annotations

from app.agent_runtime.skill_builder.eval_templates import select_eval_template


def test_select_eval_template_chooses_structured_extraction_for_action_items() -> None:
    template = select_eval_template(
        intent="회의록에서 액션 아이템을 추출하고 담당자별 표로 정리하는 스킬",
        draft_package={"description": "extract action items into a table"},
    )

    assert template.key == "structured_extraction"
    assert template.case_count == 3
    assert "schema_adherence" in template.grader_focus


def test_select_eval_template_chooses_research_for_sources_and_citations() -> None:
    template = select_eval_template(
        intent="웹 자료를 조사하고 출처와 citation을 포함해서 요약하는 스킬",
        draft_package={"description": "research assistant with source links"},
    )

    assert template.key == "research"
    assert "citation_quality" in template.grader_focus


def test_select_eval_template_defaults_to_general_task() -> None:
    template = select_eval_template(
        intent="짧은 이메일 초안을 자연스럽게 다듬는 스킬",
        draft_package={"description": "rewrite short email drafts"},
    )

    assert template.key == "general_task"
    assert template.case_count == 2
