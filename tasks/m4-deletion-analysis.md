# 삭제 분석 보고서 — M4

> 작성: 베조스 (QA) | 날짜: 2026-04-06
> 분석 범위: M4 scope (정리 + Creation Agent 교체)

---

## 즉시 삭제 가능

| 항목 | 파일 | 라인 | 이유 |
|------|------|------|------|
| `TokenTrackingCallback` 클래스 | `agent_runtime/token_tracker.py` | 전체 (9-27) | 프로덕션 코드에서 미사용. 토큰 추적은 LangGraph usage_metadata로 대체됨. grep 참조 0건. |
| SSE 파싱 로직 (trigger_executor) | `agent_runtime/trigger_executor.py` | 67-77 | `execute_agent_stream()` → `build_agent()` + `invoke()` 전환 시 불필요. delta 누적 + JSON 파싱 11줄. |
| `json` import (trigger_executor) | `agent_runtime/trigger_executor.py` | 3 | SSE 파싱 제거 후 미사용. |
| streaming.py 미들웨어 필터 | `agent_runtime/streaming.py` | 13-27, 42-76 | `_is_tool_selector_json()` + character-by-character 버퍼링. 아래 상세 판단 참조. |

---

## 수정 필요 (교체)

| 항목 | 현재 | 변경 후 | 영향 범위 |
|------|------|---------|----------|
| `creation_agent.py` `run_creation_conversation()` | `model.ainvoke()` 직접 호출 (L151, L166) | `create_deep_agent()` 기반 에이전트로 교체 | 함수 내부만 변경. 시그니처/반환 형태 유지 가능. |
| `trigger_executor.py` `execute_trigger()` | `execute_agent_stream()` SSE 스트림 소비 (L53-77) | `build_agent()` + `agent.invoke()` 직접 호출 | 함수 내부만 변경. SSE 파싱 제거, invoke 결과에서 content 추출. |
| `trigger_executor.py` import | `from executor import execute_agent_stream` (L8) | `from executor import build_agent` | import 경로 변경 |
| `creation_agent.py` import | `from model_factory import create_chat_model` (L10) | `create_deep_agent` + `create_chat_model` 조합 | import 추가 |

---

## 유지

| 항목 | 파일 | 유지 이유 |
|------|------|----------|
| `CREATION_SYSTEM_PROMPT` 상수 | `creation_agent.py` L12-141 | 4단계 에이전트 생성 워크플로우 정의. deep agent 전환 후에도 시스템 프롬프트로 사용. |
| JSON 추출/파싱 로직 | `creation_agent.py` L177-199 | `extract_json_from_markdown()`, `strip_json_blocks()` — 응답 후처리. 에이전트 프레임워크와 무관. |
| `PatchedLLMToolSelectorMiddleware` | `middleware_registry.py` L269-298 | 아래 상세 판단 참조. |
| `middleware_registry.py` 전체 | `middleware_registry.py` | `build_middleware_instances()`, `get_provider_middleware()`, `get_middleware_registry()` — executor.py, agents.py, schemas/agent.py에서 활발히 사용. |
| `streaming.py` `format_sse()` + `stream_agent_response()` | `streaming.py` | conversations.py SSE 스트리밍에서 여전히 사용. |
| `mcp_client.py` `test_mcp_connection()`, `list_mcp_tools()` | `mcp_client.py` | UI에서 MCP 서버 연결 테스트/도구 목록 조회에 사용. |
| `config.py` 전체 설정 | `config.py` | 모든 설정이 활발히 참조됨. skill_storage_dir, skill_max_package_bytes 등 M3 유지 대상 포함. |

---

## streaming.py 미들웨어 필터 판단

**결론: 삭제 가능 (불필요)**

### 근거

1. **LLMToolSelectorMiddleware는 deepagents에 의해 자동 적용되지 않음.** 사용자가 agent middleware_configs에 명시적으로 `"llm_tool_selector"`를 설정한 경우에만 활성화.

2. **필터가 방어하는 시나리오가 실제로 발생하지 않음.** LLMToolSelectorMiddleware는 `wrap_model_call()` 훅에서 실행되며, structured output(`{"tools":[...]}`)은 내부적으로 소비되고 스트림에 노출되지 않음.

3. **deepagents의 PatchToolCallsMiddleware는 다른 문제를 해결.** dangling tool call 패치이며, `{"tools":[...]}` JSON 필터링과 무관.

4. **character-by-character 버퍼링은 성능 부담.** 모든 스트리밍 청크를 글자 단위로 분석하는 로직이 실질적 이점 없이 복잡성만 추가.

### 삭제 범위
- `_is_tool_selector_json()` 함수 (L13-27)
- character-by-character 버퍼링 로직 (L42-76)
- `stream_agent_response()`를 단순화: delta를 직접 yield

---

## middleware_registry.py 패치 판단

**결론: 유지 필요**

### 근거

1. **deepagents가 const 정규화를 내부적으로 처리하지 않음.** PatchToolCallsMiddleware는 dangling tool call만 처리. GPT-4o의 `{"const": "tool_name"}` → `"tool_name"` 정규화 로직 없음.

2. **langchain LLMToolSelectorMiddleware (tool_selection.py L243-244)가 string을 기대.** const dict가 전달되면 `"Model selected invalid tools"` ValueError 발생.

3. **사용자가 llm_tool_selector를 GPT-4o와 함께 설정할 수 있음.** UI에서 미들웨어 선택 가능. 패치 제거 시 GPT-4o + llm_tool_selector 조합이 깨짐.

4. **비용 대비 효과:** 패치 코드 30줄. 제거 시 위험 > 유지 비용.

### 조치
- `PatchedLLMToolSelectorMiddleware` 유지
- `_resolve_middleware_class()` 특수 케이스 유지
- 향후 langchain이 const 처리를 내장하면 그때 제거

---

## 시드 데이터 불일치

**결론: 불일치 없음 (1건 경미한 타입 힌트 이슈)**

### 검증 결과
- M1 제거 항목 (create_mcp_tool 등): 시드에서 참조 0건 ✓
- M2 제거 항목 (Message 모델 등): 시드에서 참조 0건 ✓
- M3 제거 항목 (skill_executor 등): 시드에서 참조 0건 ✓
- 도구 시드 (17개): 전부 `tool_factory.py` `_BUILTIN_BUILDERS` / `_PREBUILT_REGISTRY`에 대응 ✓
- 모델 시드 (3개): 전부 `model_factory.py` `PROVIDER_MAP`에 대응 ✓
- 템플릿 시드 (7개): 참조 도구가 모두 시드에 존재 ✓

### 경미한 이슈
- `models/template.py` L20: `recommended_tools: Mapped[dict | None]` — 실제 데이터는 `list[str]`. 스키마에서도 `list[str] | None` 기대. 타입 힌트가 `dict`로 되어있어 불일치. 기능적 문제 없음 (JSON 컬럼은 둘 다 수용).
- **권장:** M4-S6에서 `Mapped[list | None]`로 수정.

---

## Dead Code 스캔 결과

| 항목 | 파일 | 상태 | 조치 |
|------|------|------|------|
| `TokenTrackingCallback` | `token_tracker.py` | 미사용 (grep 0건) | **삭제** |
| M1 제거 함수 참조 | 전체 | grep 0건 | ✓ 정리 완료 |
| M2 제거 모델 참조 | 전체 | grep 0건 | ✓ 정리 완료 |
| M3 제거 파일 참조 | 전체 | grep 0건 | ✓ 정리 완료 |
| 미사용 import | 전체 | 발견 없음 | ✓ 깨끗 |
| 미사용 config 설정 | `config.py` | 발견 없음 | ✓ 모두 참조됨 |
| 미사용 의존성 | `pyproject.toml` | 발견 없음 | ✓ 모두 사용됨 |
| TODO/FIXME/HACK | 전체 | M1-M3 관련 없음 | ✓ 깨끗 |

---

## 테스트 영향

| 테스트 파일 | 현재 테스트 수 | 영향 범위 | 조치 |
|------------|-------------|----------|------|
| `test_creation_agent.py` | 10 | mock 대상 변경 (`create_chat_model` → `create_deep_agent`) | 10건 전부 재작성 |
| `test_agent_creation_extended.py` | 14 | patch 경로 변경 (간접 영향) | patch decorator 수정 |
| `test_trigger_executor.py` | 11 | mock 대상 변경 (`execute_agent_stream` → `build_agent`) | 7건 재작성, 4건 유지 |
| `test_streaming.py` | — | character-by-character 필터 테스트 제거 | 필터 관련 assertion 삭제, 단순화 |
| `test_executor.py` | 14 | 영향 없음 | 유지 |

---

## creation_agent.py 상세 분석

### 현재 구조
- **단일 함수:** `run_creation_conversation()` (L144-214, 71줄)
- **패턴:** `create_chat_model("openai", "gpt-4o")` → `model.ainvoke(lc_messages)` → JSON 추출
- **도구 사용 없음:** 순수 언어 생성 (function calling 미사용)
- **상태 관리:** 외부에서 `conversation_history` 리스트로 전달 (stateless)

### 외부 참조 그래프
```
run_creation_conversation()
  ← agent_creation_service.send_message() (L53)
    ← routers/agent_creation.py POST /api/agents/create-session/{id}/message (L41-54)
```

### deep agent 전환 영향도
- **시그니처 변경 불필요:** 반환 dict 형태 유지 가능
- **핵심 변경:** L151 모델 생성 + L166 ainvoke → create_deep_agent + invoke
- **JSON 파싱 로직 유지:** L177-199 (에이전트 프레임워크와 무관)
- **서비스/라우터 변경 없음:** 인터페이스 동일

---

## trigger_executor.py 상세 분석

### 현재 구조
- **단일 함수:** `execute_trigger()` (L16-92, 77줄)
- **패턴:** `execute_agent_stream(...)` async for → SSE 파싱 → delta 누적
- **SSE 파싱:** L67-77 (11줄) — `"data: "` 접두사 파싱, JSON decode, delta/content 추출

### 외부 참조 그래프
```
execute_trigger()
  ← scheduler.py add_trigger_job() (L39, APScheduler 콜백 등록)
```

### direct invoke 전환 영향도
- **SSE 파싱 전체 제거:** L67-77 (11줄)
- **교체 패턴:** `build_agent()` + `agent.invoke({"messages": [...]}, config)` → result["messages"][-1].content
- **chat_service 호출 유지:** get_agent_with_tools, create_conversation, build_effective_prompt, build_tools_config, build_agent_skills — 전부 유지
- **DB 로직 유지:** trigger 상태 업데이트, run_count 증가

---

## 삭제 체크리스트 (S7 검증용)

### 즉시 삭제
- [ ] `token_tracker.py` 파일 삭제 (또는 TokenTrackingCallback 클래스 삭제)
- [ ] `streaming.py` `_is_tool_selector_json()` 함수 제거
- [ ] `streaming.py` character-by-character 버퍼링 로직 제거 → 단순 스트리밍으로 교체

### 교체 (S3: trigger_executor)
- [ ] `trigger_executor.py` `execute_agent_stream` import → `build_agent` import
- [ ] `trigger_executor.py` `json` import 제거
- [ ] `trigger_executor.py` SSE 파싱 루프 (L67-77) → `build_agent()` + `invoke()` 직접 호출
- [ ] `test_trigger_executor.py` mock 대상 변경 (7건): `execute_agent_stream` → `build_agent`

### 교체 (S4: creation_agent)
- [ ] `creation_agent.py` `model.ainvoke()` → `create_deep_agent()` + `invoke()`
- [ ] `creation_agent.py` import 변경
- [ ] `test_creation_agent.py` mock 대상 변경 (10건)
- [ ] `test_agent_creation_extended.py` patch 경로 수정

### 정리 (S5: streaming/middleware)
- [ ] `streaming.py` 미들웨어 필터 코드 제거 (위 즉시 삭제 항목)
- [ ] `streaming.py` 테스트 수정 (character-by-character assertion 제거)
- [ ] `PatchedLLMToolSelectorMiddleware` 유지 확인

### 정리 (S6: 시드/코드)
- [ ] `models/template.py` L20 타입 힌트 수정: `Mapped[dict | None]` → `Mapped[list | None]`
- [ ] `token_tracker.py` 삭제 확인
- [ ] 전체 ruff check 통과
- [ ] 전체 pytest 통과
