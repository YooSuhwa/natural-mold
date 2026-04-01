from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.language_models import BaseChatModel

from app.agent_runtime.model_factory import create_chat_model

CREATION_SYSTEM_PROMPT = """당신은 AI 에이전트 빌더 'Moldy'의 에이전트 설계 전문가입니다.
사용자가 원하는 에이전트를 만들 수 있도록 대화를 이끌어주세요.

## 역할
1. 사용자의 목적을 이해하기 위해 후속 질문을 합니다 (2-3개)
2. 충분한 정보가 모이면 에이전트 구성을 제안합니다
3. 구성에는 이름, 설명, 시스템 프롬프트, 추천 도구, 추천 모델이 포함됩니다

## 질문 가이드
- 어떤 업무를 자동화하고 싶은지
- 구체적인 규칙이나 기준이 있는지
- 어떤 외부 서비스와 연결이 필요한지

## 응답 형식
일반 대화 중에는 자연스러운 한국어로 질문하세요.

에이전트 구성을 제안할 준비가 되면, 응답 마지막에 반드시 아래 JSON 블록을 포함하세요:
```json
{
  "draft_config": {
    "name": "에이전트 이름",
    "description": "에이전트 설명",
    "system_prompt": "시스템 프롬프트 전체 내용",
    "recommended_tool_names": ["도구1", "도구2"],
    "recommended_model": "GPT-4o",
    "is_ready": true
  }
}
```

사용자의 설명이 모호하면 구체적인 예시를 들며 재질문하세요.
"""


async def run_creation_conversation(
    conversation_history: list[dict[str, str]],
    user_message: str,
    available_tools: list[str] | None = None,
    available_models: list[str] | None = None,
) -> dict[str, Any]:
    model = create_chat_model("openai", "gpt-4o")

    system_content = CREATION_SYSTEM_PROMPT
    if available_tools:
        system_content += f"\n\n## 사용 가능한 도구\n{', '.join(available_tools)}"
    if available_models:
        system_content += f"\n\n## 사용 가능한 모델\n{', '.join(available_models)}"

    messages = [{"role": "system", "content": system_content}]

    for msg in conversation_history:
        messages.append(msg)

    messages.append({"role": "user", "content": user_message})

    # Convert to LangChain messages
    lc_messages = []
    for msg in messages:
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))
        elif msg["role"] == "system":
            from langchain_core.messages import SystemMessage
            lc_messages.append(SystemMessage(content=msg["content"]))

    response = await model.ainvoke(lc_messages)
    content = response.content

    # Try to extract draft_config from JSON block
    draft_config = None
    import json
    import re
    json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', content)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            if "draft_config" in parsed:
                draft_config = parsed["draft_config"]
        except json.JSONDecodeError:
            pass

    return {
        "role": "assistant",
        "content": content,
        "draft_config": draft_config,
    }
