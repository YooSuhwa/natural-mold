"""ask_user — 사용자에게 질문하고 응답을 기다리는 도구.

LangGraph interrupt()를 사용하여 그래프 실행을 일시정지하고,
사용자의 응답을 받은 후 Command(resume=)로 재개한다.
"""

from langchain_core.tools import tool
from langgraph.types import interrupt


@tool
def ask_user(question: str, options: list[str] | None = None) -> str:
    """사용자에게 질문하고 응답을 기다립니다.

    다음 상황에서만 사용하세요:
    - 사용자의 요청이 모호하여 2가지 이상 해석이 가능할 때
    - 중요한 작업 실행 전 최종 확인이 필요할 때
    - 여러 옵션 중 사용자의 선호를 확인해야 할 때

    다음 상황에서는 사용하지 마세요:
    - 일반적인 질문에 답변할 때 (바로 답하세요)
    - 이미 충분한 정보가 있을 때
    - 단순한 인사나 잡담

    Args:
        question: 사용자에게 보여줄 질문
        options: 선택지 목록 (없으면 자유 입력)
    """
    response = interrupt(
        {
            "type": "ask_user",
            "question": question,
            "options": options or [],
        }
    )
    return str(response)
