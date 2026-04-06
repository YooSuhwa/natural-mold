# ADR-004: M4 정리 — Creation Agent + Trigger + Streaming

## 상태: 승인됨

## 맥락

M1-M3에서 deep agent 엔진 전환이 완료되었다. M4에서 남은 코드를 정리하고 통일된 엔진을 사용한다.

정리 대상 3가지:
1. **creation_agent.py** — `model.ainvoke()` 직접 호출. deep agent 미사용.
2. **trigger_executor.py** — `execute_agent_stream()` 호출 후 SSE 파싱. 스트리밍 불필요한데 SSE 인코딩/디코딩 왕복 발생.
3. **streaming.py / middleware_registry.py** — 미들웨어 JSON 필터와 PatchedLLMToolSelectorMiddleware가 deep agent 전환 후에도 필요한지 판단.

---

## 결정 1: creation_agent → create_deep_agent 전환

### 선택: 전환하되 최소 변경 (checkpointer 미사용)

```python
# Before (creation_agent.py)
model = create_chat_model("openai", "gpt-4o")
lc_messages = convert_to_langchain_messages(messages)
response = await model.ainvoke(lc_messages)
content = response.content

# After
from app.agent_runtime.executor import build_agent

model = create_chat_model("openai", "gpt-4o")
agent = build_agent(model, tools=[], system_prompt=system_content)
lc_messages = convert_to_langchain_messages(history + [user_msg])
result = await agent.ainvoke({"messages": lc_messages})
content = result["messages"][-1].content
```

**설계 결정:**

| 항목 | 결정 | 이유 |
|------|------|------|
| 도구 | `tools=[]` (빈 리스트) | 도구 사용 없음. create_deep_agent는 `tools or []` 처리로 빈 리스트 허용 |
| Checkpointer | **미사용** (None) | 대화 히스토리는 `agent_creation_sessions.conversation_history` DB JSON 필드에서 관리. checkpointer 도입 시 creation session의 데이터 모델 변경 필요 → 불필요한 복잡성 |
| 호출 방식 | `agent.ainvoke()` | SSE 스트리밍 불필요. 응답 전체를 한 번에 수신 |
| 메시지 전달 | 매 호출마다 전체 히스토리 전달 | checkpointer 없이 히스토리를 agent에 전달. system_prompt는 create_deep_agent 파라미터로 분리 |
| JSON 파싱 | **기존 유지** | `extract_json_from_markdown()` + `strip_json_blocks()`. `response_format` 사용 시 프론트엔드 변경 필요 → PoC에서 과도 |
| Backend/Skills/Memory | 미사용 | creation agent는 파일시스템 접근 불필요 |

**근거:**
- **통일된 엔진**: 모든 LLM 호출이 `create_deep_agent` 경로를 통과. 미들웨어(프롬프트 캐싱, 모더레이션) 자동 적용 가능.
- **최소 변경**: checkpointer 미도입으로 기존 DB 스키마와 서비스 로직(agent_creation_service.py) 변경 불필요.
- **향후 확장**: 도구 추가(템플릿 브라우징 등)가 필요할 때 `tools=[...]`만 전달하면 됨.

**변경 파일:**

| 파일 | 변경 |
|------|------|
| `creation_agent.py` | `model.ainvoke()` → `build_agent()` + `agent.ainvoke()` |
| `agent_creation_service.py` | 변경 없음 (인터페이스 동일) |

### 대안 분석

**대안 A: Checkpointer 사용**
- creation_session_id를 thread_id로 사용하여 checkpointer에 히스토리 저장
- 장점: DB JSON 필드 제거 가능, 완전한 통합
- 단점: agent_creation_sessions 테이블 스키마 변경, agent_creation_service.py 대폭 수정, creation session과 일반 conversation의 checkpointer 혼재
- 판단: 비용 대비 이점 부족 → 기각

**대안 B: 현행 유지 (model.ainvoke 직접 호출)**
- 장점: 변경 없음, 가장 단순
- 단점: 유일하게 create_deep_agent를 사용하지 않는 LLM 호출 경로. 미들웨어 적용 불가
- 판단: "통일된 엔진" 목표 미달성 → 기각

**대안 C: response_format으로 구조화된 출력**
- `create_deep_agent(response_format=CreationResponse)` 사용
- 장점: JSON 파싱 로직 제거, 타입 안전
- 단점: 현재 프론트엔드가 문자열 content + 별도 JSON 필드 기대. Pydantic 모델 정의 + 프론트엔드 수정 필요
- 판단: M4 스코프 초과 → 향후 과제

---

## 결정 2: trigger_executor → direct invoke 전환

### 선택: `_prepare_agent()` 추출 + `execute_agent_invoke()` 추가 (옵션 A+C 혼합)

**현재 문제:**
```python
# trigger_executor.py — 현재
async for chunk in execute_agent_stream(...):
    for line in chunk.strip().split("\n"):
        if line.startswith("data: "):
            data = json.loads(line[6:])    # SSE 디코딩
            if "delta" in data:
                full_content += data["delta"]
```

스트리밍이 필요 없는데 SSE 인코딩(streaming.py) → SSE 디코딩(trigger_executor.py) 왕복이 발생한다.

**변경 후 구조:**

```python
# executor.py

async def _prepare_agent(
    provider, model_name, api_key, base_url,
    system_prompt, tools_config, messages_history, thread_id,
    model_params, middleware_configs, agent_skills, agent_id,
) -> tuple[Any, list, dict]:
    """에이전트 빌드 + 설정. stream/invoke 공용."""
    model = create_chat_model(provider, model_name, api_key, base_url, **(model_params or {}))
    langchain_tools = ...   # 기존 도구 빌드 로직
    mcp_tools = ...         # MCP 도구 빌드
    middleware = ...         # 미들웨어 빌드
    backend = ...            # FilesystemBackend
    skills_sources = ...     # skills 소스
    memory_sources = ...     # memory 소스
    agent = build_agent(model, langchain_tools, system_prompt, ...)
    lc_messages = convert_to_langchain_messages(messages_history)
    config = {"configurable": {"thread_id": thread_id}}
    return agent, lc_messages, config


async def execute_agent_stream(...) -> AsyncGenerator[str, None]:
    """스트리밍 실행 (채팅용)."""
    agent, lc_messages, config = await _prepare_agent(...)
    async for chunk in stream_agent_response(agent, lc_messages, config):
        yield chunk


async def execute_agent_invoke(...) -> str:
    """비스트리밍 실행 (트리거용). 최종 응답 텍스트만 반환."""
    agent, lc_messages, config = await _prepare_agent(...)
    result = await agent.ainvoke({"messages": lc_messages}, config=config)
    messages = result.get("messages", [])
    if messages and hasattr(messages[-1], "content"):
        return messages[-1].content
    return ""
```

```python
# trigger_executor.py — 변경 후
from app.agent_runtime.executor import execute_agent_invoke

full_content = await execute_agent_invoke(
    provider=agent.model.provider,
    model_name=agent.model.model_name,
    ...
)
```

**설계 결정:**

| 항목 | 결정 | 이유 |
|------|------|------|
| 공통화 방식 | `_prepare_agent()` 내부 함수 추출 | 도구/미들웨어/백엔드 빌드 로직이 ~50줄. 중복 제거 |
| 호출 방식 | `agent.ainvoke()` | CompiledStateGraph는 invoke/ainvoke 완전 지원. 트리거는 결과만 필요 |
| 반환 타입 | `str` (최종 content) | 트리거는 전체 응답 텍스트만 필요. 메시지 메타데이터 불필요 |
| `execute_agent_stream` | 시그니처 유지 | 기존 호출자(conversations.py) 변경 없음 |

**근거:**
- **SSE 왕복 제거**: 인코딩/디코딩 불필요 → 코드 단순화 + 미미한 성능 개선
- **코드 공유**: `_prepare_agent()`로 에이전트 빌드 로직 단일화. 향후 다른 실행 모드(배치 등) 추가 용이
- **trigger_executor 단순화**: SSE 파싱 ~15줄 → 함수 호출 1줄

**변경 파일:**

| 파일 | 변경 |
|------|------|
| `executor.py` | `_prepare_agent()` 추출, `execute_agent_invoke()` 추가, `execute_agent_stream()` 내부 리팩터 |
| `trigger_executor.py` | `execute_agent_stream()` → `execute_agent_invoke()` 호출로 교체. SSE 파싱 제거 |

### 대안 분석

**대안 A: _prepare_agent() 추출만 (invoke 함수 없음)**
- trigger_executor가 직접 `_prepare_agent()` + `agent.ainvoke()` 호출
- 단점: trigger_executor가 executor 내부 구조(agent state format, message extraction)를 알아야 함
- 판단: 캡슐화 부족 → 기각

**대안 B: trigger_executor 독립 구현**
- 도구/미들웨어 빌드를 trigger_executor 내부에서 직접 구현
- 단점: ~50줄 코드 중복
- 판단: DRY 위반 → 기각

**대안 C: execute_agent_invoke() 추가만 (리팩터 없음)**
- execute_agent_stream()과 별도로 전체 setup 코드를 복제
- 단점: 중복 → 유지보수 부담
- 판단: 기각 (A와 결합하여 채택)

---

## 결정 3: streaming/middleware 정리

### 선택: 둘 다 유지

#### 3-1. streaming.py — 미들웨어 JSON 필터 유지

**조사 결과:**
- `PatchToolCallsMiddleware`는 `before_agent()` 훅만 구현 (메시지 히스토리의 dangling tool call 패치)
- **스트림 이벤트를 필터링하지 않음** — `wrap_model_call()`이나 스트리밍 후처리 없음
- 따라서 `LLMToolSelectorMiddleware`가 `{"tools": ["tool1", "tool2"]}` JSON을 모델 응답으로 생성하면, 그대로 스트림에 노출됨

**결정**: streaming.py의 `_is_tool_selector_json()` + character-by-character 버퍼링 필터를 **유지**한다.

```python
# streaming.py — 유지 대상
def _is_tool_selector_json(text: str) -> bool:
    """LLMToolSelectorMiddleware 출력 감지. PatchToolCallsMiddleware가
    스트림 필터링을 하지 않으므로 이 필터가 여전히 필요."""
```

#### 3-2. middleware_registry.py — PatchedLLMToolSelectorMiddleware 유지

**조사 결과:**
- GPT-4o가 structured output에서 `{"const": "tool_name"}` 객체를 반환하는 이슈는 GPT-4o 고유 동작
- deepagents는 도구 스키마를 `tools or []`로 전달할 뿐, 선택 응답 정규화를 내부 처리하지 않음
- `LLMToolSelectorMiddleware._process_selection_response()`가 문자열만 기대하므로 dict 입력 시 오류

**결정**: `PatchedLLMToolSelectorMiddleware`를 **유지**한다.

```python
# middleware_registry.py — 유지 대상
class PatchedLLMToolSelectorMiddleware(LLMToolSelectorMiddleware):
    """GPT-4o의 {"const": "name"} 형식을 문자열로 정규화.
    deepagents 내부에서 미처리. 상위 langchain 패키지에서 수정될 때까지 유지."""
```

### 유지 사유 요약

| 컴포넌트 | 유지 사유 | 재검토 시점 |
|----------|----------|------------|
| `_is_tool_selector_json()` + 버퍼링 | PatchToolCallsMiddleware가 스트림 미필터링 | deepagents가 스트림 필터링 내장 시 |
| `PatchedLLMToolSelectorMiddleware` | GPT-4o `{"const": "name"}` 이슈 미수정 | langchain 또는 deepagents에서 정규화 내장 시 |

### 유일한 변경: 코드 주석 추가

기존 코드에 유지 사유를 주석으로 명시하여 향후 재검토를 용이하게 한다 (코드 로직 변경 없음).

---

## 결과

### 긍정적
- **통일된 엔진**: creation_agent도 `create_deep_agent` 경로 사용. 프로젝트 내 모든 LLM 호출이 단일 엔진.
- **SSE 왕복 제거**: trigger_executor가 `ainvoke()` 직접 호출. ~15줄 SSE 파싱 코드 제거.
- **코드 공유**: `_prepare_agent()`로 에이전트 빌드 로직 단일화.
- **안전한 정리**: streaming/middleware는 조사 결과에 근거하여 유지. 사용자에게 내부 JSON이 노출되는 regression 방지.

### 부정적
- **creation_agent 오버헤드**: 단순 LLM 호출에 graph 컴파일 비용 추가. 실제 체감 영향은 미미하나 기술적 오버헤드 존재.
- **미들웨어 코드 잔존**: streaming.py 필터와 PatchedLLMToolSelectorMiddleware가 workaround로 남음. 상위 패키지 수정 시까지 유지보수 필요.

### 향후 과제 (M4 이후)
- [ ] creation_agent에 `response_format` 도입 검토 (프론트엔드 변경과 함께)
- [ ] deepagents 스트림 필터링 기능 추가 시 streaming.py 필터 제거
- [ ] langchain LLMToolSelectorMiddleware의 `{"const"}` 처리 수정 시 패치 제거
- [ ] creation_agent에 도구 추가 (템플릿 검색, 스킬 카탈로그 브라우징 등)
