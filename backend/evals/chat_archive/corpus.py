"""Deterministic synthetic long-form research conversations for archive measurement."""

from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

TOPICS = (
    "지역 도서관 이용률",
    "공공자전거 거치대",
    "청소년 박물관 교육",
    "하천 수질 관측",
    "소규모 상점 배송",
    "도시 숲 관리",
    "마을회관 냉난방",
    "시내버스 환승",
    "여름 축제 대기 시간",
    "생활 폐기물 수거",
    "학교 급식 잔반",
    "문화센터 접근성",
    "시장 통행량",
    "공유 공구 대여",
    "공동주택 충전기",
    "체육시설 예약",
    "해안 산책로 정비",
    "농산물 직거래",
    "주민센터 강좌",
    "농촌 이동 진료",
)
ACTIONS = (
    "원자료의 지역별 차이를 정리해 줘.",
    "누락치를 대조해 줘.",
    "분기별 변화 근거를 붙여 줘.",
)
ZONES = ("동부", "서부", "남부", "북부", "중앙")
NOTES = ("측정 정상", "검토 중", "장비 교체 후 측정", "우천으로 운영 축소")


def messages_for_round(case: int, period: int) -> list[AnyMessage]:
    """Return the next three queries, tool invocations, CSV responses and answers."""
    topic = TOPICS[case]
    result: list[AnyMessage] = []
    for turn, action in enumerate(ACTIONS):
        prefix = f"report-{case}-{period}-{turn}"
        row_count = 3_800 + case * 47 + turn * 133
        records = [
            f"{topic} | 연도,지역,표본ID,측정값,변동률,관찰 메모",
            *(
                f"{2022 + period},{ZONES[row % len(ZONES)]},{prefix}-{row},"
                f"{(row * 19 + case) % 997},{row % 11 - 5}%,"
                f"{NOTES[(row + turn) % len(NOTES)]} & 자료 검수 완료"
                for row in range(row_count)
            ),
        ]
        result.extend(
            [
                HumanMessage(content=f"{2022 + period}년 {topic}. {action}", id=f"{prefix}-user"),
                AIMessage(
                    content="",
                    id=f"{prefix}-call",
                    tool_calls=[
                        {
                            "id": f"{prefix}-query",
                            "name": "regional_statistics",
                            "args": {"topic": topic, "year": 2022 + period, "sheet": turn},
                        }
                    ],
                ),
                ToolMessage(
                    content="\n".join(records),
                    name="regional_statistics",
                    tool_call_id=f"{prefix}-query",
                    id=f"{prefix}-table",
                ),
                AIMessage(
                    content=f"{topic}의 {row_count}건 점검. 요청 사항과 근거 기록.",
                    id=f"{prefix}-answer",
                ),
            ]
        )
    return result
