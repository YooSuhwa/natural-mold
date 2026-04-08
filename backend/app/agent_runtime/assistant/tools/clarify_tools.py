"""Assistant 명확화 도구 — ask_clarifying_question (LangGraph interrupt 패턴).

사용자에게 옵션 3개 + 직접입력으로 질문을 보낸다.
"""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool


def build_clarify_tools() -> list[StructuredTool]:
    """Assistant 명확화 도구 1개를 생성한다."""

    async def ask_clarifying_question(
        question: str,
        option_1: str,
        option_2: str,
        option_3: str,
    ) -> str:
        """사용자에게 명확화 질문을 합니다 (옵션 3개 + 직접 입력).

        모호한 요청에는 추측 대신 이 도구를 사용하세요.
        한 응답에 정확히 1개 질문만 허용됩니다.

        Args:
            question: 사용자에게 물어볼 질문
            option_1: 첫 번째 선택지
            option_2: 두 번째 선택지
            option_3: 세 번째 선택지
        """
        return json.dumps(
            {
                "type": "clarifying_question",
                "question": question,
                "options": [option_1, option_2, option_3, "직접 입력"],
            },
            ensure_ascii=False,
        )

    return [
        StructuredTool.from_function(
            coroutine=ask_clarifying_question,
            name="ask_clarifying_question",
            description="사용자에게 명확화 질문 (옵션 3개 + 직접입력). 모호한 요청 시 사용.",
        ),
    ]
