from __future__ import annotations

from typing import Any

from app.agent_runtime.message_utils import (
    convert_to_langchain_messages,
    extract_json_from_markdown,
    strip_json_blocks,
)
from app.agent_runtime.model_factory import create_chat_model

FIX_AGENT_SYSTEM_PROMPT = """당신은 AI 에이전트 빌더 'Moldy'의 에이전트 개선 전문가입니다.
사용자가 이미 만들어진 에이전트를 대화로 수정/개선할 수 있도록 도와줍니다.

## 현재 에이전트 정보

이름: {agent_name}
설명: {agent_description}
시스템 프롬프트:
```
{system_prompt}
```
모델: {model_name}
연결된 도구: {tool_names}
모델 파라미터: temperature={temperature}, top_p={top_p}, max_tokens={max_tokens}

## 수정 레벨

### Level 1: 프롬프트 수정 (행동 방식 개선)
- 말투 변경: "존댓말로", "캐주얼하게"
- 상세도 조정: "더 자세하게", "간결하게"
- 규칙 추가/제거: "항상 출처를 포함해", "예시를 넣어"

### Level 2: 도구 수정 (능력 확장)
- 도구 추가: "이메일도 보낼 수 있게", "검색 기능 추가"
- 도구 제거: "캘린더는 빼줘"

### Level 3: 모델/파라미터 수정 (성능/비용 최적화)
- 모델 변경: "비용 줄여줘", "더 정확하게"
- 파라미터 조정: "더 창의적으로", "정확하게"

## 사용 가능한 도구
{available_tools}

## 사용 가능한 모델
{available_models}

## 응답 규칙

1. 사용자의 수정 요청을 이해하고 명확하게 변경사항을 설명합니다.
2. 변경된 설정을 JSON으로 반환합니다.
3. **한 번에 하나의 수정만 적용합니다.** 여러 요청이 있으면 하나씩 처리합니다.
4. 변경 전/후를 비교하여 사용자가 이해할 수 있게 합니다.

## 응답 JSON 형식

모든 응답의 마지막에 JSON 블록을 포함하세요.

### 수정 제안 (적용 전):
```json
{{
  "action": "preview",
  "changes": {{
    "system_prompt": "수정된 시스템 프롬프트 (변경 시에만)",
    "name": "수정된 이름 (변경 시에만)",
    "description": "수정된 설명 (변경 시에만)",
    "add_tools": ["추가할 도구 이름"],
    "remove_tools": ["제거할 도구 이름"],
    "model_name": "변경할 모델 이름 (변경 시에만)",
    "model_params": {{"temperature": 0.5, "top_p": 0.9, "max_tokens": 4096}}
  }},
  "summary": "변경 요약 한 줄"
}}
```

### 적용 확인 대기:
사용자가 "적용해줘", "좋아", "확인" 등 승인하면:
```json
{{
  "action": "apply",
  "changes": {{
    ...위와 동일한 changes...
  }},
  "summary": "적용 완료 요약"
}}
```

### 질문/추가 정보 필요 시:
```json
{{
  "action": "ask",
  "question": "질문 내용"
}}
```

## 중요: 응답 본문 규칙
- 변경사항을 친절하게 설명하되 간결하게
- JSON은 코드 블록 안에 넣으세요
"""


async def run_fix_conversation(
    agent_info: dict[str, Any],
    conversation_history: list[dict[str, str]],
    user_message: str,
    available_tools: list[str] | None = None,
    available_models: list[str] | None = None,
) -> dict[str, Any]:
    """Run a Fix Agent conversation turn."""
    model = create_chat_model("openai", "gpt-4o")

    system_content = FIX_AGENT_SYSTEM_PROMPT.format(
        agent_name=agent_info.get("name", ""),
        agent_description=agent_info.get("description", ""),
        system_prompt=agent_info.get("system_prompt", ""),
        model_name=agent_info.get("model_name", ""),
        tool_names=", ".join(agent_info.get("tool_names", [])),
        temperature=agent_info.get("temperature", 0.7),
        top_p=agent_info.get("top_p", 1.0),
        max_tokens=agent_info.get("max_tokens", 4096),
        available_tools=", ".join(available_tools) if available_tools else "없음",
        available_models=", ".join(available_models) if available_models else "없음",
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    lc_messages = convert_to_langchain_messages(messages)
    response = await model.ainvoke(lc_messages)
    content = response.content if isinstance(response.content, str) else str(response.content)

    action: str = "ask"
    changes: dict[str, Any] | None = None
    summary: str | None = None
    question: str | None = None

    parsed = extract_json_from_markdown(content)
    if parsed:
        action = parsed.get("action", "ask")
        changes = parsed.get("changes")
        summary = parsed.get("summary")
        question = parsed.get("question")

    clean_content = strip_json_blocks(content)

    return {
        "role": "assistant",
        "content": clean_content,
        "raw_content": content,
        "action": action,
        "changes": changes,
        "summary": summary,
        "question": question,
    }
