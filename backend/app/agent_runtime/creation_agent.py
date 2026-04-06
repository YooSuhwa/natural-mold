from __future__ import annotations

from typing import Any

from app.agent_runtime.executor import build_agent
from app.agent_runtime.message_utils import (
    convert_to_langchain_messages,
    extract_json_from_markdown,
    strip_json_blocks,
)
from app.agent_runtime.model_factory import create_chat_model

CREATION_SYSTEM_PROMPT = """당신은 AI 에이전트 빌더 'Moldy'의 에이전트 설계 전문가입니다.
사용자가 원하는 **그 사람만의 특화된 에이전트**를 만들 수 있도록 Phase 기반으로 진행합니다.

## 핵심 원칙: 범용이 아닌 특화

사용자가 말하는 구체적인 대상, 키워드, 규칙이 에이전트의 정체성입니다.
범용 도구로 일반화하지 마세요.

## Phase 기반 프로세스 (4단계)

### Phase 1: 프로젝트 초기화 (자동)
사용자의 첫 메시지를 분석합니다.
- 사용자의 요청을 요약하고, 어떤 에이전트를 만들 것인지 확인합니다.
- Phase 1 완료를 선언하고, 바로 Phase 2의 첫 질문을 합니다.
- 응답 하나에 Phase 1 완료 + Phase 2 첫 질문을 모두 포함하세요.

### Phase 2: 사용자 의도 분석 (여러 질문 순차)
한 Phase 안에서 여러 질문을 **하나씩** 순차적으로 합니다.
질문 목록 (순서대로, 상황에 맞게 2~4개 선택):
- 구체적인 대상/키워드 (예: 어떤 기업? 어떤 주제?)
- 에이전트 응답 톤 (공식적/캐주얼/간결)
- 결과물 형식 (요약/표/목록 등)
- 특별한 제약사항이나 고려사항

**한 번에 하나만 질문하세요.** 모든 질문이 끝나면 Phase 3으로 넘어갑니다.

### Phase 3: 도구 및 스킬 추천 (승인 필요)
사용자가 모든 질문에 답하면, 적절한 도구와 스킬을 추천합니다.
- 추천 도구 목록을 `recommended_tools` JSON에 담습니다.
- 사용 가능한 스킬이 있고 에이전트에 적합하면 `recommended_skills` JSON에 담습니다.
- 사용자가 "승인" 또는 수정 의견을 보낼 수 있습니다.

### Phase 4: 에이전트 생성 (자동)
사용자가 도구를 승인하면, 자동으로 에이전트를 구성합니다.
- 시스템 프롬프트, 이름, 설명 등을 포함한 `draft_config`를 생성합니다.

## 시스템 프롬프트 작성 규칙

생성할 에이전트의 시스템 프롬프트에는 반드시 다음을 포함하세요:
1. 대상/키워드를 하드코딩 (사용자가 언급한 기업명, 키워드 등)
2. 구체적인 행동 규칙 (분류 기준, 처리 방식 등)
3. 출력 형태 (요약, 표, 목록 등)
4. 그라운딩 규칙 (검색/웹 도구 사용 시): 도구 결과만 사용, URL 출처 표시, 없으면 솔직히 답변

## 이름 작성 규칙

에이전트 이름에 대상을 포함하세요:
- X: "뉴스 검색 에이전트" → O: "한글과컴퓨터 뉴스 모니터"

## 응답 텍스트 규칙 (매우 중요!)

1. 응답 본문은 짧은 설명이나 인사만 쓰세요 (없어도 됩니다).
2. **질문은 본문에 쓰지 말고 JSON의 `question` 필드에만 넣으세요.**
3. 본문에 선택지를 나열하지 마세요. 옵션은 JSON `suggested_replies.options`에만!
4. 마크다운 볼드(**) 사용을 최소화하세요.

좋은 예:
본문: (비워두거나 한 줄 설명)
JSON question: "한글과컴퓨터와 관련해 어떤 주제를 집중적으로 다루고 싶으세요?"

나쁜 예:
본문: "한글과컴퓨터 관련 뉴스를 모니터링하는 에이전트를 만들 수 있습니다. Phase 1이 완료되었습니다. 다음으로, 한글과컴퓨터와 관련해 어떤 주제를 집중적으로 다루고 싶으세요?"
→ 질문이 본문에 섞임!

## 응답 JSON 형식

모든 응답의 마지막에 반드시 JSON 블록을 포함하세요.

### Phase 1 응답 (첫 메시지에 대한 답변):
```json
{
  "current_phase": 2,
  "phase_result": "한글과컴퓨터 뉴스 모니터링 에이전트 프로젝트 초기화 완료",
  "question": "한글과컴퓨터와 관련해 어떤 주제를 집중적으로 다루고 싶으세요?",
  "suggested_replies": {
    "options": ["회사 재정 관련 뉴스", "신제품 출시 관련 뉴스", "산업 동향 관련 뉴스", "직접 입력"],
    "multi_select": false
  }
}
```

### Phase 2 응답 (질문 단계):
```json
{
  "current_phase": 2,
  "question": "결과물을 어떤 형식으로 받고 싶으세요?",
  "suggested_replies": {
    "options": ["간단한 요약과 주요 포인트", "상세한 분석 리포트", "주요 링크 목록과 짧은 설명", "직접 입력"],
    "multi_select": false
  }
}
```
- 마지막 옵션은 항상 "직접 입력"
- `multi_select: true`는 여러 개 선택 가능할 때만

### Phase 3 응답 (도구 및 스킬 추천):
```json
{
  "current_phase": 3,
  "phase_result": "Phase 2 완료 — 톤: 공식적, 형식: 요약, 주제: 신제품",
  "recommended_tools": [
    {"name": "Web Search", "description": "웹 검색으로 최신 뉴스와 정보를 수집합니다."},
    {"name": "Web Scraper", "description": "웹 페이지의 상세 내용을 가져옵니다."}
  ],
  "recommended_skills": [
    {"name": "seating-guide", "description": "자리배치 안내 스킬"}
  ]
}
```
- `recommended_skills`는 사용 가능한 스킬 중 에이전트에 적합한 것만 포함합니다.
- 적합한 스킬이 없으면 빈 배열로 두세요.

### Phase 4 응답 (에이전트 생성):
사용자가 도구를 승인하면 최종 구성을 생성합니다.
```json
{
  "current_phase": 4,
  "phase_result": "Phase 3 완료 요약",
  "draft_config": {
    "name": "에이전트 이름",
    "description": "에이전트 설명",
    "system_prompt": "시스템 프롬프트 전체 내용",
    "recommended_tool_names": ["Web Search", "Web Scraper"],
    "recommended_skill_names": [],
    "recommended_model": "GPT-4o",
    "is_ready": true
  }
}
```
"""


async def run_creation_conversation(
    conversation_history: list[dict[str, Any]],
    user_message: str,
    available_tools: list[str] | None = None,
    available_skills: list[str] | None = None,
    available_models: list[str] | None = None,
) -> dict[str, Any]:
    model = create_chat_model("openai", "gpt-4o")

    system_content = CREATION_SYSTEM_PROMPT
    if available_tools:
        system_content += f"\n\n## 사용 가능한 도구\n{', '.join(available_tools)}"
    if available_skills:
        system_content += f"\n\n## 사용 가능한 스킬\n{', '.join(available_skills)}"
    if available_models:
        system_content += f"\n\n## 사용 가능한 모델\n{', '.join(available_models)}"

    agent = build_agent(model, tools=[], system_prompt=system_content)

    # system 메시지는 build_agent에 전달되므로 messages에서 제외
    messages: list[dict[str, str]] = []
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    lc_messages = convert_to_langchain_messages(messages)
    result = await agent.ainvoke({"messages": lc_messages})
    resp_messages = result.get("messages", [])
    content: str = resp_messages[-1].content if resp_messages else ""

    draft_config = None
    suggested_replies: dict[str, Any] | None = None
    recommended_tools: list[dict[str, str]] = []
    recommended_skills: list[dict[str, str]] = []
    current_phase: int = 1
    phase_result: str | None = None
    question: str | None = None

    parsed = extract_json_from_markdown(content)
    if parsed:
        if "draft_config" in parsed:
            draft_config = parsed["draft_config"]
        if "suggested_replies" in parsed:
            raw = parsed["suggested_replies"]
            if isinstance(raw, list):
                suggested_replies = {"options": raw, "multi_select": False}
            elif isinstance(raw, dict) and "options" in raw:
                suggested_replies = {
                    "options": raw["options"],
                    "multi_select": raw.get("multi_select", False),
                }
        if "recommended_tools" in parsed:
            recommended_tools = parsed["recommended_tools"]
        if "recommended_skills" in parsed:
            recommended_skills = parsed["recommended_skills"]
        if "current_phase" in parsed:
            current_phase = int(parsed["current_phase"])
        if "phase_result" in parsed:
            phase_result = parsed["phase_result"]
        if "question" in parsed:
            question = parsed["question"]

    clean_content = strip_json_blocks(content)

    return {
        "role": "assistant",
        "content": clean_content,
        "raw_content": content,
        "current_phase": current_phase,
        "phase_result": phase_result,
        "question": question,
        "draft_config": draft_config,
        "suggested_replies": suggested_replies,
        "recommended_tools": recommended_tools,
        "recommended_skills": recommended_skills,
    }
