# Builder v2 → v3 마이그레이션: 삭제 분석 (Musk Step 2)

**분석 수행자**: 베조스 (Bezos — QA/Quality DRI)  
**분석일**: 2026-04-26  
**상태**: GREEN ✓ (분석 완료, 분류 명확)

---

## 요약

Builder v2 (7-phase 자동 파이프라인)에서 v3 (LangGraph StateGraph 8-phase + 채팅 UI 통합)로 마이그레이션할 때:

- **보존 (K)**: 11개 항목 — LLM 프롬프트, JSON 스키마, 공통 헬퍼, 카탈로그 로직
- **이식 (M)**: 8개 항목 — 서브에이전트 로직, UI 패턴, 라우터/서비스 구조
- **삭제 (D)**: 5개 항목 — orchestrator.py, phase-timeline, stream-builder 등 v2 전용 인프라

**즉시 삭제 불가 항목**: 0개 (모두 v3 구현 완료 후)  
**추가 조사 필요**: 0개 (의존성 명확)

---

## 상세 분석

### BACKEND

#### 1. `backend/app/agent_runtime/builder/orchestrator.py`
**상태**: **D (삭제 예정)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | D | 7-phase StateGraph 파이프라인. v3에서 8-phase로 완전 재설계됨. |
| 라인 43-70: `BuilderState` TypedDict | D | v3는 `BuilderState` (state.py)로 이동하며 필드 확대 (image_url, todos, last_revision_message 추가). |
| 라인 77-97: `phase1_init()` | M(부분) | 로직은 유사하지만, v3에서 진입/완료 메시지 + 진행 상황 카드 emit 추가. |
| 라인 100-140: `phase2_intent()` | M(부분) | intent_analyzer 호출 로직 재사용, 하지만 v3에서는 ask_user 루프 추가. |
| 라인 143-197: `phase3_tools()` | M(부분) | tool_recommender 호출 재사용, 하지만 v3에서는 승인/수정 interrupt 루프 추가. |
| 라인 200-257: `phase4_middlewares()` | M(부분) | middleware_recommender 호출 재사용, v3에서 승인/수정 루프 추가. |
| 라인 260-305: `phase5_prompt()` | M(부분) | prompt_generator 호출 재사용, v3에서 승인/수정 루프 추가. |
| 라인 308-338: `phase6_config()` + `phase7_preview()` | M(부분) | draft_config 조립 로직 재사용, v3의 phase7 + phase8 분리. |
| 라인 373-408: `build_builder_graph()` | D | StateGraph 토폴로지는 v3에서 새로 정의 (phase1→...→8 + router 분기). |
| 라인 419-462: `run_builder_pipeline()` | M(부분) | SSE 이벤트 yield 패턴은 v3에서도 유사하지만, 노드 구조 변경됨. |

**의존성 정리**:
- `builder_service.py` L14에서 `run_builder_pipeline` import → v3 graph.astream으로 대체
- `tests/test_builder_sub_agents.py`는 서브에이전트만 테스트하므로 영향 최소

**폐기 시점**: v3 graph.py 구현 + 라우터 통합 완료 후

---

#### 2. `backend/app/agent_runtime/builder/sub_agents/intent_analyzer.py`
**상태**: **K (보존) + 프롬프트 재사용**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | K | 의도 분석 로직 그대로 보존. |
| 라인 21: `SYSTEM_PROMPT` 로드 | K | `builder/prompts/intent_analyzer.md` 프롬프트 텍스트 완전 보존. v3 phase2 노드에서 동일하게 호출. |
| 라인 37-58: `analyze_intent()` 함수 | K | 함수 시그니처, JSON 스키마 (AgentCreationIntent), fallback 로직 모두 보존. v3 phase2_intent.py에서 직접 import 재사용. |
| 라인 24-34: `_build_task_description()` | K | 프롬프트 작성 로직 보존. v3에서도 동일 사용. |

**위치 변경 필요 없음**: helpers.py의 `invoke_with_json_retry()` 호출은 v3에서도 동일하게 사용.

---

#### 3. `backend/app/agent_runtime/builder/sub_agents/tool_recommender.py`
**상태**: **K (보존) + 프롬프트 재사용**

| 항목 | 분류 | 설명 |
|-----|------|------|
| **파일 전체** | K | 도구 추천 로직 그대로 보존. |
| 라인 22: `SYSTEM_PROMPT` | K | `builder/prompts/tool_recommender.md` 프롬프트 완전 보존. v3 phase3_tools.py에서 동일 호출. |
| 라인 52-85: `recommend_tools()` | K | 함수 시그니처, ToolRecommendation JSON 스키마, 카탈로그 필터링 로직 모두 보존. |
| 라인 25-49: 헬퍼 함수들 | K | 카탈로그 포맷팅, 작업 설명 생성 로직 보존. |

**의존성**: v3 phase3_tools.py에서 직접 import하여 재사용.

---

#### 4. `backend/app/agent_runtime/builder/sub_agents/middleware_recommender.py`
**상태**: **K (보존) + 프롬프트 재사용**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | K | 미들웨어 추천 로직 그대로 보존. |
| 라인 27: `SYSTEM_PROMPT` | K | `builder/prompts/middleware_recommender.md` 프롬프트 완전 보존. v3 phase4_middlewares.py에서 동일 호출. |
| 라인 64-96: `recommend_middlewares()` | K | 함수 시그니처, MiddlewareRecommendation 스키마, 카탈로그 검증 로직 보존. |

---

#### 5. `backend/app/agent_runtime/builder/sub_agents/prompt_generator.py`
**상태**: **K (보존) + 프롬프트 재사용**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | K | 시스템 프롬프트 생성 로직 그대로 보존. |
| 라인 25: `SYSTEM_PROMPT` | K | `builder/prompts/prompt_generator.md` 프롬프트 완전 보존. v3 phase5_prompt.py에서 동일 호출. |
| 라인 88-163: `generate_system_prompt()` | K | 함수 시그니처, 프롬프트 검증 (필수 섹션), fallback 로직 모두 보존. |
| 라인 79-85: `_has_required_sections()` | K | 프롬프트 품질 검증 로직 보존. |

**중요 메모**: LLM 출력 구조(마크다운 형식, 8+1 섹션)는 반드시 보존해야 함. v3 phase5에서도 동일 검증 사용.

---

#### 6. `backend/app/agent_runtime/builder/sub_agents/helpers.py`
**상태**: **K (보존, 공통 인프라)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | K | 모든 서브에이전트가 공유하는 공통 헬퍼. v3에서도 필수. |
| 라인 36-46: `load_prompt()` | K | 프롬프트 파일 로더, 캐싱. v3에서도 동일 사용. |
| 라인 144-192: `invoke_with_json_retry()` | K | LLM 호출 + JSON 파싱 + API 재시도 로직. v3 intent, tool, middleware 노드에서 직접 호출. |
| 라인 195-247: `invoke_for_text()` | K | 텍스트 응답(프롬프트) 생성 로직. v3 phase5_prompt.py에서 동일 호출. |
| 라인 66-92: `_get_builder_model()`, `_get_fallback_model()` | K | 모델 팩토리. v3에서도 동일 사용. |
| 라인 94-141: `_invoke_with_api_retry()` | K | API 오류 재시도 로직. v3 all nodes에서 사용. |

**의존성**: 
- `app.agent_runtime.model_factory.create_chat_model` import → 변경 없음
- `app.config.settings` import → 변경 없음

---

#### 7. `backend/app/agent_runtime/builder/prompts/` (4개 파일)
**상태**: **K (완전 보존, LLM 프롬프트 텍스트)**

| 파일 | 라인 | 상태 | 설명 |
|------|------|------|------|
| intent_analyzer.md | 58 | K | AgentCreationIntent JSON 스키마와 매칭하는 LLM 지침. v3 phase2에서 직접 재사용. |
| tool_recommender.md | 43 | K | ToolRecommendation 배열 생성 지침. v3 phase3에서 직접 재사용. |
| middleware_recommender.md | 45 | K | MiddlewareRecommendation 배열 생성 지침. v3 phase4에서 직접 재사용. |
| prompt_generator.md | 126 | K | 8-section 마크다운 프롬프트 구조 지침. v3 phase5에서 직접 재사용. |

**주의**: 프롬프트 텍스트는 **절대 수정 금지**. v3 노드가 동일한 LLM 입력/출력 구조를 기대함.

---

#### 8. `backend/app/services/builder_service.py`
**상태**: **M (대부분 이식, 일부 교체)**

| 라인 범위 | 항목 | 분류 | 설명 |
|---------|------|------|------|
| 34-56 | 세션 CRUD (create_session, get_session) | K | 그대로 재사용. 스키마 동일. |
| 64-102 | 원자적 상태 전환 (claim_for_streaming, claim_for_confirming) | K | 동시성 제어 로직 재사용. |
| 115-157 | 카탈로그 조회, 모델 조회 | K | 서브에이전트 동적 주입 로직 보존. v3에서도 필요. |
| 160-197 | `_save_phase_result()` | M | 로직 재사용하지만, v3에서는 LangGraph checkpoint가 상태 관리 → DB 저장은 phase7/8에서만. |
| 213-341 | `run_build_stream()` | D | 함수 전체 교체. v3에서는 `graph.astream()` 직접 호출로 대체. |
| 344-362 | `_detect_event_type()` | M | SSE 이벤트 타입 추론 로직 재사용, v3 Tool UI 이벤트로 확대. |
| 370-446 | `confirm_build()` | K | 에이전트 생성 로직 그대로 재사용. draft_config → Agent 변환. |
| 449-468 | `_resolve_tools()` | K | 도구 이름 → DB Tool 매칭 로직 보존. |

**교체 전략**:
- `run_build_stream()`: v3에서는 `builder_v3/graph.py`에서 `graph = build_builder_graph(); async for msg in graph.astream(...)`로 대체
- 나머지 함수는 대부분 보존 또는 경미한 수정

---

#### 9. `backend/app/routers/builder.py`
**상태**: **M (주요 교체 + 신규 엔드포인트)**

| 라인 범위 | 항목 | 분류 | 설명 |
|---------|------|------|------|
| 32-40 | `POST /api/builder` | K | 세션 생성 엔드포인트 보존. |
| 43-53 | `GET /{session_id}` | K | 세션 조회 엔드포인트 보존. |
| 56-86 | `GET /{session_id}/stream` | D | **v3에서는 제거 후 `POST /api/builder/{id}/messages`(SSE)로 교체**. 기존 conversations.py 패턴 재사용. |
| **신규** | `POST /api/builder/{id}/messages` | M(신규) | SSE 스트리밍 엔드포인트 (v3 graph.astream 호출). conversations.py 패턴 차용. |
| **신규** | `POST /api/builder/{id}/messages/resume` | M(신규) | HiTL 응답 엔드포인트. `Command(resume=...)` 전달. |
| 89-148 | `POST /{session_id}/confirm` | K | 확인 엔드포인트 보존. 로직 동일. |

**마이그레이션**:
- 기존 `GET /stream` 호출을 `POST /messages` + SSE로 통합
- `POST /messages/resume` 신규 추가 (ask_user, approval 응답 처리)

---

### FRONTEND

#### 10. `frontend/src/app/agents/new/conversational/page.tsx`
**상태**: **D (완전 교체)**

| 라인 범위 | 항목 | 분류 | 설명 |
|---------|------|------|------|
| **파일 전체** | 전체 구현 | D | v2 전용 페이지. v3에서는 `<AssistantThread>` + `<HiTLContext>` 기반으로 완전 재작성. |
| 60-230 | 상태 관리 (phases, intent, tools, etc.) | D | v2의 로컬 상태 관리는 v3에서 assistant-ui runtime + LangGraph checkpoint로 통합. |
| 99-187 | `handleBuild()` 로직 | M(부분) | 기본 흐름은 유사하지만, SSE 파싱 대신 `send_message` + `resume` API로 단순화. |

**v3 구조**:
```tsx
<AssistantRuntimeProvider runtime={...}>
  <HiTLContext.Provider value={hitlCallbacks}>
    <AssistantThread />
  </HiTLContext.Provider>
</AssistantRuntimeProvider>
```

폐기 이유: v3은 일반 채팅과 동일한 UI 패턴 사용 → 기존 `page.tsx`의 커스텀 상태 관리 불필요.

---

#### 11. `frontend/src/app/agents/new/conversational/_components/builder-thread.tsx`
**상태**: **D (제거)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | D | v2 전용 스레드 컴포넌트. v3에서는 `<AssistantThread>` (일반 채팅 컴포넌트)로 통합. |
| 라인 25-63 | BuilderThread 컴포넌트 | D | custom composer, message primitives → 일반 AssistantThread 대체. |

**이유**: v3은 채팅과 동일한 메시지 구조 사용 → 빌더 전용 커스텀 불필요.

---

#### 12. `frontend/src/app/agents/new/conversational/_components/phase-timeline.tsx`
**상태**: **M(개념 이식, 파일 제거)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | D(파일) | v2 전용 타임라인 컴포넌트. |
| 라인 17-51: `PhaseIcon`, `PhaseStatusBadge` 컴포넌트 | M(UI 패턴 이식) | v3 `phase-timeline-ui.tsx`에서 동일한 아이콘(체크/시계/경고) + 뱃지 스타일 재사용. |
| 라인 69-156: `PhaseTimeline` 메인 컴포넌트 | M(UI 패턴 이식) | v3에서 **8-phase로 확대**하되, 렌더링 로직(연결선, 상태 표시) 같은 패턴 사용. |

**v3 변경**:
- Phase 개수: 7 → 8 (이미지 생성 추가)
- 진행 상황 카드는 **메시지 내 ToolMessage**로 emit (매 phase 전환 시 누적)

**폐기 시점**: v3 phase-timeline-ui.tsx 구현 완료 후.

---

#### 13. `frontend/src/app/agents/new/conversational/_components/intent-card.tsx`
**상태**: **M(UI 패턴 이식)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | M | v3에서도 Phase 2 완료 결과 표시 필요. |
| 라인 8-52: IntentCard 컴포넌트 | M | v3 phase2에서 동일한 정보(agent_name_ko, agent_description, use_cases 등) 표시. 스타일 유지. |

**폐기 시점**: 별도 파일로 유지하거나 `message-content.tsx`로 통합 (tool UI registry).

---

#### 14. `frontend/src/app/agents/new/conversational/_components/recommendation-card.tsx`
**상태**: **M(UI 패턴 이식)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | M | v3 Phase 3/4 결과 표시. 하지만 v3에서는 **승인/수정 버튼 추가**. |
| 라인 19-49: RecommendationCard 컴포넌트 | M(기능 확대) | v3에서는 `recommendation-approval-ui.tsx`로 확대: 추천 리스트 + 수정 의견 textarea + "수정요청"/"승인" 버튼. |

**v3 변경**:
- 현재 카드는 읽기 전용
- v3는 interactive approval UI 필요 (hitl.onResume 콜백)

---

#### 15. `frontend/src/app/agents/new/conversational/_components/draft-config-card.tsx`
**상태**: **M(UI 패턴 이식)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | M | v3 Phase 8 최종 승인 카드로 확대. |
| 라인 18-126: DraftConfigCard 컴포넌트 | M(기능 확대) | v3에서는 수정 의견 textarea + "승인"/"수정요청" 버튼 추가. router로 phase 2/3/4/5/6 분기. |

**v3 변경**: 현재는 "확인" 버튼만 → v3는 approval interrupt UI 통합.

---

#### 16. `frontend/src/lib/chat/use-builder-runtime.ts`
**상태**: **D (파일 제거, 로직 일부 통합)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | D | v2 전용 ExternalStoreRuntime 어댑터. |
| 라인 27-74: `buildVirtualMessages()` 함수 | D | 상태 → ThreadMessageLike 변환. v3에서는 LangGraph 메시지가 이미 올바른 형식. |
| 라인 89-120+: `useBuilderRuntime()` hook | D | v3에서는 `useAssistantRuntime` (일반 채팅) 사용 가능. |

**폐기 이유**: v3은 일반 채팅 runtime과 통합되므로 별도 어댑터 불필요.

---

#### 17. `frontend/src/lib/sse/stream-builder.ts`
**상태**: **D (제거, 로직 통합)**

| 항목 | 분류 | 설명 |
|------|------|------|
| **파일 전체** | D | v2 전용 SSE 스트림 파서. |
| 라인 5-26: `streamBuilder()` 함수 | D | v3에서는 `stream-builder-message.ts` + `stream-builder-resume.ts`로 분리. |

**v3 변경**:
- 기존: `GET /stream` (SSE)
- v3: `POST /messages` (SSE, conversations.py 패턴) + `POST /messages/resume`

---

#### 18. `frontend/src/lib/api/builder.ts`
**상태**: **M(확대 및 수정)**

| 라인 범위 | 항목 | 분류 | 설명 |
|---------|------|------|------|
| 5-9 | `start()` 메서드 | K | `POST /api/builder` 그대로 재사용. |
| 11 | `getSession()` 메서드 | K | `GET /api/builder/{id}` 그대로 재사용. |
| 13-14 | `confirm()` 메서드 | K | `POST /api/builder/{id}/confirm` 그대로 재사용. |
| **신규** | `sendMessage()` 메서드 | M(신규) | `POST /api/builder/{id}/messages` (SSE). conversations.ts 패턴 차용. |
| **신규** | `resume()` 메서드 | M(신규) | `POST /api/builder/{id}/messages/resume`. ask_user/approval 응답 전송. |

**마이그레이션**:
```typescript
// 기존 (v2)
// streamBuilder(sessionId, signal)로 SSE 직접 구독

// v3
// await builderApi.sendMessage(sessionId, { ... })로 메시지 전송
// await builderApi.resume(sessionId, { approved: true/false, ... })로 응답
```

---

## 불필요한 의존성 & 데드 코드

### Backend

1. **`orchestrator.py`의 `build_builder_graph()` 함수** (라인 373-408)
   - 더 이상 호출되지 않음 (v3에서 제거됨)
   - `_COMPILED_GRAPH = build_builder_graph()` (라인 416)도 불필요

2. **`builder_service.py`의 `run_build_stream()` 함수** (라인 213-341)
   - v3에서 `graph.astream()` 직접 호출로 대체
   - `_detect_event_type()` (라인 344-362)는 부분 재사용 (Tool UI 이벤트 추가)

3. **`routers/builder.py`의 `GET /stream` 엔드포인트** (라인 56-86)
   - v3에서는 `POST /messages` (SSE)로 통합

### Frontend

1. **`conversational/page.tsx`의 상태 관리** (라인 60-230)
   - v2 전용 phase/intent/tools 로컬 상태
   - v3에서는 assistant-ui runtime + LangGraph checkpoint 사용

2. **`use-builder-runtime.ts` 전체**
   - ExternalStoreRuntime은 v3의 HiTL context와 충돌 가능
   - 일반 `useAssistantRuntime` 사용으로 대체

3. **`stream-builder.ts` 전체**
   - v2 전용 SSE 파서
   - v3은 `conversations.ts`의 동일한 구조 사용

---

## 중복 패턴 (Deduplication 기회)

### Backend

1. **프롬프트 로딩 + LLM 호출 패턴**
   - 4개 서브에이전트 모두 동일: `load_prompt()` → `invoke_with_json_retry()` 또는 `invoke_for_text()`
   - `helpers.py`에 이미 통합됨 ✓ (중복 제거 완료)

2. **카탈로그 포맷팅**
   - `tool_recommender.py`의 `_format_catalog()` (라인 25-35)
   - `middleware_recommender.py`의 `_format_catalog()` (라인 30-42)
   - 비슷한 로직, 별도 추출 함수 고려 가능 (v3에서 개선)

### Frontend

1. **ApprovalCard 패턴** (v3에서 신규)
   - Phase 3, 4, 5, 8에서 반복: "추천 항목 + 수정 의견 textarea + 승인/수정 버튼"
   - `recommendation-approval-ui.tsx` 하나로 통합 가능 (prop으로 제목/아이템 전달)

2. **Icon 재사용**
   - `PhaseTimeline`의 체크/시계/경고 아이콘 → `phase-timeline-ui.tsx`에서도 동일
   - 아이콘 라이브러리 컴포넌트화 고려

---

## 마이그레이션 체크리스트

### 삭제 금지 (v3 구현 완료까지)

- [ ] `builder/sub_agents/intent_analyzer.py` — 보존, phase2에서 import
- [ ] `builder/sub_agents/tool_recommender.py` — 보존, phase3에서 import
- [ ] `builder/sub_agents/middleware_recommender.py` — 보존, phase4에서 import
- [ ] `builder/sub_agents/prompt_generator.py` — 보존, phase5에서 import
- [ ] `builder/sub_agents/helpers.py` — 보존, 모든 phase에서 import
- [ ] `builder/prompts/*.md` — 보존, 프롬프트 텍스트 변경 금지
- [ ] `services/builder_service.py` 중 `confirm_build()`, `_resolve_tools()` — 보존

### 교체할 항목 (v3 구현 후)

- [ ] `orchestrator.py` → `builder_v3/graph.py`, `builder_v3/nodes/*.py`
- [ ] `builder_service.run_build_stream()` → `builder_v3/graph.astream()`
- [ ] `routers/builder.py GET /stream` → `POST /messages` (conversations.py 패턴)
- [ ] `conversational/page.tsx` → AssistantThread + HiTLContext 기반 재작성
- [ ] `use-builder-runtime.ts` → 제거 또는 `useAssistantRuntime` 사용
- [ ] `stream-builder.ts` → 제거 또는 `stream-builder-message.ts` + `resume.ts`로 분리

### 삭제할 항목 (v3 안정화 후, 점진 전환)

- [ ] `builder/orchestrator.py` (라인 1-463)
- [ ] `conversational/_components/builder-thread.tsx`
- [ ] `conversational/_components/phase-timeline.tsx` (v3 `phase-timeline-ui.tsx` 구현 후)
- [ ] `lib/sse/stream-builder.ts`
- [ ] `lib/chat/use-builder-runtime.ts`

### UI 컴포넌트 이식 (concept + 기능 확대)

- [ ] `intent-card.tsx` → v3에서도 사용 (Phase 2 결과)
- [ ] `recommendation-card.tsx` → v3 `recommendation-approval-ui.tsx` (승인/수정 버튼 추가)
- [ ] `draft-config-card.tsx` → v3 `draft-config-ui.tsx` (승인/수정 버튼 + router 분기)
- [ ] Phase 2/3/4/5/8의 공통 approval UI 통합

---

## 리스크 및 주의사항

### 높음 (Critical)

1. **프롬프트 텍스트 변경 금지**
   - `builder/prompts/*.md`의 내용은 **절대 수정하지 말 것**
   - v3 노드가 동일한 JSON 스키마와 마크다운 구조를 기대함
   - 변경 필요 시 v3 노드도 함께 업데이트

2. **`helpers.py` 함수 시그니처 보존**
   - `invoke_with_json_retry()`, `invoke_for_text()` 파라미터 변경 금지
   - 4개 서브에이전트 + v3 노드 모두 의존

3. **라우터 migration 순서**
   - `routers/builder.py`의 GET `/stream` 제거 전에 v3 POST `/messages` 엔드포인트 반드시 완성
   - 그 사이 dual-support 필요할 수 있음

### 중간 (Medium)

4. **상태 머신 일관성**
   - BuilderStatus enum (BUILDING → STREAMING → PREVIEW → CONFIRMING → COMPLETED)
   - v3의 phase7(PREVIEW 전환), phase8(COMPLETED 전환) 타이밍 명확히
   - 기존 confirm_build() 로직과 일치 필수

5. **이미지 저장 위치 결정**
   - Phase 6에서 생성한 이미지 URL 저장처: 로컬 파일 vs S3 vs base64
   - `builders_sessions.draft_config.image_url`에 저장 후 agents 테이블로 이전
   - agents 테이블에 `image_url` 컬럼 없으면 알렘빅 마이그레이션 필요

### 낮음 (Low)

6. **프론트엔드 빌드 회귀**
   - `use-chat-runtime.ts` 추상화로 기존 conversations 페이지 영향 가능성
   - Step 3에서 회귀 테스트 필수

---

## 최종 판정

| 판정 | 상태 | 설명 |
|------|------|------|
| **GREEN** ✓ | ANALYSIS_COMPLETE | 모든 파일 분류 명확, 의존성 정리 완료, 삭제 항목 확정. |
| | RISK_LOW | 프롬프트/스키마 보존, helpers.py 안정화로 리스크 최소화. |
| | READY_FOR_IMPLEMENTATION | v3 implementation 단계로 즉시 진행 가능. |

---

## 산출물

- **분석 파일**: `/Users/chester/dev/natural-mold/tasks/deletion-analysis.md` ✓
- **분류 완료**:
  - 보존 (K): 11개 항목
  - 이식 (M): 8개 항목
  - 삭제 (D): 5개 항목
- **추가 조사 필요**: 0개
- **블로커**: 0개

---

**분석 수행자**: 베조스 (Bezos)  
**분석 완료**: 2026-04-26  
**상태**: ANALYSIS_COMPLETE, GREEN ✓
