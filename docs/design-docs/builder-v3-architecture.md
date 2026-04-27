# ADR-001: Builder v3 LangGraph StateGraph 8-Phase 아키텍처

**상태**: 제안됨  
**작성자**: Pichai (Architecture DRI)  
**날짜**: 2026-04-26  
**영향범위**: `backend/app/agent_runtime/builder_v3/`, `frontend/src/app/agents/new/conversational/`, 라우터/서비스

---

## 맥락

### 현재 상황 (Builder v2)

- **분리된 시스템**: Builder는 `executor.py`/`create_deep_agent()`를 사용하지 않고, 자체 `orchestrator.py` 파이프라인 + `invoke_with_json_retry()` 로직으로 동작
- **HiTL 미구현**: 사용자가 "사용자에게 물어볼 것"을 선택해도 백엔드가 그냥 결과를 받아 다음 phase로 진행
- **UI 분리**: 자체 `PhaseTimeline` + 카드들 사용, 메시지 히스토리 없음
- **두 가지 사용자 보고 버그**:
  1. 단계 진행이 보이지 않음 → 이벤트가 한 배열로 push되어 거의 동시 도착
  2. 사용자에게 되물어보는데 통과해버림 → interrupt 호출 자체 없음

### 근본 원인

두 문제는 모두 **채팅 인프라(HiTL, SSE, checkpointer) 미통합**에서 비롯.  
기존 채팅은 `interrupt()`/`Command(resume=...)`/checkpointer로 완벽히 동작하고 있는데, Builder는 자체 구현으로 이를 모두 피해간 상태.

### 해결책의 필요성

- **HiTL 구현**: 각 phase(특히 3/4/5/6/8)에서 승인/수정요청 루프 필요
- **채팅 UI 통합**: 기존 `assistant-thread.tsx` 재사용 가능 → 일관된 UX, 유지보수 수월
- **진행 상황 가시성**: 매 phase 전환 시 메시지 안에 카드 emit → mockup 이미지와 정렬
- **순서 강제**: 그래프 토폴로지로 phase 순서를 코드 레벨에서 보장 (LLM이 어길 수 없음)

---

## 결정

### 1. 기술 선택: StateGraph (ReAct 대신)

| 기준 | ReAct (create_deep_agent) | StateGraph |
|------|--------------------------|-----------|
| **순서 강제** | LLM이 도구 선택 → 이탈 가능 | 그래프 엣지로 위상 강제 |
| **HiTL** | 지원함 | 지원함 |
| **Checkpoint** | 지원함 | 지원함 |
| **Interrupt 복원** | 지원함 | 지원함 |
| **8-phase 보장** | X (LLM이 건너뛸 수 있음) | O (모든 노드를 거쳐야 함) |

**선택**: **StateGraph** — mockup 이미지의 엄격한 진행 순서를 보장하기 위해

### 2. 8-Phase 구조

```
[START]
  ↓
Phase 1: 프로젝트 초기화 (자동, LLM 불필요)
  ↓
Phase 2: 사용자 의도 분석 (ask_user 루프 — 이름/설명 부족 시)
  ↓
Phase 3: 도구 추천 (approval/revision 루프)
  ↓
Phase 4: 미들웨어 추천 (approval/revision 루프)
  ↓
Phase 5: 시스템 프롬프트 작성 (approval/revision 루프)
  ↓
Phase 6: 에이전트 이미지 생성 (skip/generate, 재생성 루프)
  ↓
Phase 7: 설정 저장 (자동, PREVIEW 전환)
  ↓
Phase 8: 최종 승인 (approval → 수정 시 router → phase 2~6 점프)
  ↓
[END]
```

**기존 7-phase vs 신규 8-phase**:
- Phase 1~5: 기존과 동일 (단, HiTL 추가)
- Phase 6: **신규** — 에이전트 이미지 생성 (nano-banana 또는 OpenAI Image API)
- Phase 7~8: 기존의 Phase 6~7을 Phase 7~8로 리넘버링

### 3. BuilderState TypedDict 시그니처

```python
from langchain_core.messages import BaseMessage, add_messages
from typing_extensions import Annotated, TypedDict

class BuilderState(TypedDict):
    """LangGraph StateGraph의 상태 컨테이너."""
    
    # 메시지 히스토리 (기존 채팅과 동일)
    messages: Annotated[list[BaseMessage], add_messages]
    
    # Phase별 중간 결과
    user_request: str                           # 초기 사용자 입력
    intent: dict | None                         # Phase 2: AgentCreationIntent
    tools: list[dict] | None                    # Phase 3: [{"name": "...", "description": "..."}, ...]
    middlewares: list[dict] | None              # Phase 4: [{"type": "...", "config": {...}}, ...]
    system_prompt: str | None                   # Phase 5: 프롬프트 텍스트
    image_url: str | None                       # Phase 6: "https://... or /api/agents/.../image.png"
    draft_config: dict | None                   # Phase 7: 최종 에이전트 설정 (name, description, tools, etc.)
    
    # 진행 상황
    todos: list[PhaseTodo]                      # 8개 phase 진행 상황 (매 노드 emit)
    current_phase: int                          # 현재 phase (1~8), 참고용
    
    # HiTL/재시도용
    last_revision_message: str | None           # Phase 3/4/5/6 수정요청 시 LLM에 전달
    last_approved_data: dict | None             # Phase 3/4/5 이전 승인 데이터 (재시도 컨텍스트)

class PhaseTodo(TypedDict):
    """진행 상황 카드 항목."""
    phase_id: int                               # 1~8
    phase_name: str                             # "의도 분석", "도구 추천", ...
    status: Literal["pending", "in_progress", "completed"]
    description: str                            # 옵션, 결과 요약
```

### 4. 8개 노드 시그니처

각 phase는 별도 모듈(`nodes/phase{1..8}.py`)로 구현:

```python
# 기본 시그니처 — 모든 노드가 이 패턴
async def phase_X(state: BuilderState) -> dict | Command:
    """
    Phase X 로직을 수행한다.
    
    1. 진입 메시지 emit (AIMessage)
    2. 진행 상황 카드 update
    3. 작업 수행 (LLM call, interrupt, etc.)
    4. 완료 메시지 emit
    5. State 갱신 후 return
    
    주의: 노드 함수는 idempotent해야 함 (interrupt 후 resume 시 재진입)
    """
```

#### Phase 1: 프로젝트 초기화

```python
async def phase1_init(state: BuilderState) -> dict:
    """
    - 진입 메시지: "이제 프로젝트를 초기화하겠습니다"
    - 진행 상황 업데이트: phase 1을 in_progress로
    - 작업: 디렉토리/파일 생성, builder_session 업데이트
    - 완료 메시지: "[Phase 1 완료] 프로젝트 초기화됨"
    - Return: state를 일부 갱신 (project_path, etc.)
    """
```

#### Phase 2: 사용자 의도 분석 (ask_user 루프)

```python
async def phase2_intent(state: BuilderState) -> dict | Command:
    """
    - 진입 메시지
    - 의도 분석 LLM call (invoke_with_json_retry 사용)
    - AgentCreationIntent 추출: {name, description, ...}
    - 누락 항목 확인:
      - 부족하면: interrupt({"type": "ask_user", "question": "이름?", "options": [...]})
      - LLM이 선택지 3-4개 생성 + "직접 입력" fallback
    - resume 응답으로 state.intent 갱신, 재확인 루프
    - 모두 채워질 때까지 반복 (노드 내부 while 루프)
    - 완료 메시지: "[Phase 2 완료] 의도 분석 완료: 이름={name}"
    """
```

#### Phase 3/4/5: 추천/생성 + 승인/수정 루프

```python
async def phase3_tools(state: BuilderState) -> dict | Command:
    """
    - 진입 메시지
    - 도구 추천 LLM call (기존 sub_agent 이식)
    - 결과 카드 emit (ToolMessage with tool_name="recommendation-approval")
    - 승인 interrupt: interrupt({
        "type": "approval",
        "data": {"tools": [...]},
        "summary": "4개 도구 추천: ...",
        "allow_revision": True
      })
    - resume 응답 분류:
      - {"approved": True}: 다음 phase로
      - {"approved": False, "revision_message": "..."}: 
        * state.last_revision_message 업데이트
        * 같은 phase 내부 재실행 (while 루프)
        * LLM에 이전 결과 + revision_message 전달하여 재생성
        * 새로운 결과 카드 emit 후 다시 interrupt
    - 승인될 때까지 반복
    - 완료 메시지: "[Phase 3 완료] 도구 추천 완료"
    """
    
    # Phase 4, 5도 동일 패턴 (generate → card → interrupt → revise loop)
```

#### Phase 6: 에이전트 이미지 생성 (신규)

```python
async def phase6_image(state: BuilderState) -> dict | Command:
    """
    - 진입 메시지: "이제 에이전트의 이미지를 생성하겠습니다"
    - auto_prompt 생성: LLM이 intent/name/description 기반으로 이미지 프롬프트 자동 생성
    
    - 1차 interrupt (skip/generate 선택):
      interrupt({
        "type": "choice",
        "title": "에이전트 이미지를 생성하시겠습니까?",
        "options": ["넘어가기", "생성하기"],
        "context": {"auto_prompt": "..."}
      })
    
    - resume 응답: {"choice": "skip"} or {"choice": "generate"}
    
    - "skip" → state.image_url=None → 다음 phase
    - "generate":
      * image_gen.py 호출 → nano-banana로 이미지 생성 (60s timeout)
      * 생성 실패 시: 사용자에게 폴백 제시 ("다시 시도" or "넘어가기")
      * 이미지 저장: backend/uploads/agent_images/{builder_session_id}.png
      * 미리보기 emit: ToolMessage with tool_name="image-generation-preview"
      
      * 2차 interrupt (확정/재생성/넘어가기):
        interrupt({
          "type": "approval",
          "data": {"image_url": "...", "prompt": "..."},
          "options": ["확정", "재생성", "넘어가기"],
          "allow_prompt_edit": True
        })
      
      * resume 응답:
        - {"choice": "confirm"} → state.image_url 저장 → 다음 phase
        - {"choice": "regenerate", "extra": {"prompt_edit": "..."}} → 다시 생성 (루프)
        - {"choice": "skip"} → state.image_url=None → 다음 phase
    
    - 완료 메시지: "[Phase 6 완료] 이미지 생성 완료" (또는 "넘어감")
    """
```

#### Phase 7: 설정 저장

```python
async def phase7_save(state: BuilderState) -> dict:
    """
    - draft_config 조립:
      {
        "name": state.intent["agent_name"],
        "description": state.intent["agent_description"],
        "tools": state.tools,
        "middlewares": state.middlewares,
        "system_prompt": state.system_prompt,
        "image_url": state.image_url,
        ...
      }
    - builder_session 업데이트: status=PREVIEW, draft_config=...
    - 완료 메시지: "[Phase 7 완료] 설정 저장됨"
    """
```

#### Phase 8: 최종 승인 + 빌드

```python
async def phase8_build(state: BuilderState) -> dict | Command:
    """
    - DraftConfigCard ToolMessage emit (전체 설정 표시)
    - interrupt({
        "type": "approval",
        "data": state.draft_config,
        "summary": "에이전트 생성 준비 완료",
        "allow_revision": True
      })
    
    - resume 응답:
      - {"approved": True}:
        * builder_session.status = COMPLETED
        * Agent 실제 생성 (또는 confirm 엔드포인트 위임)
        * 완료 메시지: "[Phase 8 완료] 에이전트 생성 완료"
        * return {}  → END
      
      - {"approved": False, "revision_message": "..."}:
        * router 노드로 분기
    """
```

#### Router: Phase 8 수정 요청 분류

```python
async def router(state: BuilderState) -> str:
    """
    Phase 8에서 사용자가 수정을 요청한 경우, 분류 LLM으로 어느 phase로
    돌아갈지 결정한다.
    
    - revision_message를 LLM으로 분류 (구조화 출력, Pydantic enum)
    - 분류 대상: "phase2" | "phase3" | "phase4" | "phase5" | "phase6"
    - 모호하면: ask_user fallback ("어느 단계를 수정하시겠습니까?" + 선택지)
    - return: 해당 phase 이름 ("phase3" 등)
    
    Phase 8 → router → (조건 분기) → phase 2/3/4/5/6 재진입 → ... → phase 8 재도착
    """
```

### 5. Interrupt Payload 계약 (3종류)

#### ask_user

```python
# Node에서 emit:
interrupt({
    "type": "ask_user",
    "question": str,          # "에이전트 이름이 뭔가요?"
    "options": list[str]      # ["웹 검색", "데이터 분석", "직접 입력"]
})

# 프론트에서 UI: user-input-ui.tsx (기존 그대로)
# 사용자 응답:
"웹 검색"  # or 직접 입력한 텍스트
```

#### approval (Phase 3/4/5/6/8)

```python
# Node에서 emit:
interrupt({
    "type": "approval",
    "data": dict,             # 승인 대상 데이터 (tools, prompt, image_url, draft_config, etc.)
    "summary": str,           # 요약 텍스트
    "allow_revision": bool    # True이면 "수정 의견" textarea 활성화
})

# 프론트에서 UI: 
#   - Phase 3/4: recommendation-approval-ui (추천 항목 + textarea + 수정/승인 버튼)
#   - Phase 5: prompt-approval-ui (프롬프트 + textarea + 수정/승인 버튼)
#   - Phase 6: image-generation-ui 2단계 (이미지 + (선택) prompt textarea + 확정/재생성/넘어가기)
#   - Phase 8: draft-config-ui (전체 설정 + textarea + 수정/승인 버튼)

# 사용자 응답:
{"approved": True}
# 또는
{"approved": False, "revision_message": "..."}
```

#### choice (Phase 6 1단계)

```python
# Node에서 emit:
interrupt({
    "type": "choice",
    "title": str,             # "에이전트 이미지를 생성하시겠습니까?"
    "options": list[str],     # ["넘어가기", "생성하기"]
    "context": dict           # {"auto_prompt": "..."} 등
})

# 프론트에서 UI: image-generation-ui 1단계 (auto_prompt 미리보기 + 버튼 2개)

# 사용자 응답:
{"choice": "skip"} or {"choice": "generate"}
# 또는 (Phase 6 2단계):
{"choice": "confirm"} or {"choice": "regenerate", "extra": {"prompt_edit": "..."}} or {"choice": "skip"}
```

### 6. Resume Payload 계약

Resume은 프론트의 `useHiTL().onResume(payload)` → `/api/builder/{id}/messages/resume` POST 엔드포인트로 전달:

```python
# ask_user 응답:
str  # e.g., "웹 검색에이전트" (옵션 또는 직접 입력)

# approval 응답:
{"approved": bool, "revision_message": str | None}

# choice 응답:
{"choice": str, "extra": dict | None}  # extra는 regenerate 시 prompt_edit 포함 가능
```

### 7. SSE 이벤트 형식 (기존 streaming.py 호환)

NoCodeGraph 노드 함수에서 `state["messages"]` 갱신 → 기존 `streaming.py`의 `stream_agent_response` 함수가 그대로 처리:

| 이벤트 | 페이로드 | 용도 |
|--------|----------|------|
| `message_start` | `{"id": "...", "role": "assistant"}` | 메시지 시작 |
| `content_delta` | `{"delta": "텍스트"}` | 스트리밍 텍스트 |
| `tool_call_start` | `{"tool_name": "...", "parameters": {...}}` | 도구 호출 시작 |
| `tool_call_result` | `{"tool_name": "...", "result": "..."}` | 도구 결과 |
| `interrupt` | `{"interrupt_id": "...", "value": {...}}` | HiTL interrupt 감지 |
| `message_end` | `{"usage": {...}, "content": "..."}` | 메시지 종료 |
| `error` | `{"message": "..."}` | 에러 발생 |

**기존 streaming.py와의 호환성**:
- StateGraph 노드에서 메시지를 `state["messages"]` 리스트에 추가 → `add_messages` 자동 병합
- `agent.astream(input, config, stream_mode="messages")` → 기존과 동일하게 SSE 이벤트로 변환
- interrupt 감지도 `agent.aget_state()` → `state.tasks[].interrupts[]` 동일 로직

### 8. 이미지 생성 인프라

#### Provider 선택

```python
# backend/app/agent_runtime/builder_v3/image_gen.py

class ImageProvider(str, Enum):
    NANOBANAN = "nanobanan"         # Gemini Flash Image (권장, 빠름)
    OPENAI = "openai"               # OpenAI DALL-E 3 (고품질, 느림)
    GOOGLE = "google"               # Google Imagen 2

# 환경 변수로 선택:
# BUILDER_IMAGE_PROVIDER=nanobanan (기본값)
# BUILDER_IMAGE_PROVIDER=openai (폴백)

async def generate_image(
    prompt: str,
    provider: ImageProvider = ImageProvider.NANOBANAN,
    fallback_provider: ImageProvider | None = ImageProvider.OPENAI,
    timeout: int = 60
) -> str | None:
    """
    이미지 생성 및 저장.
    
    Return: 저장된 이미지의 접근 가능 URL (또는 base64)
    Timeout 초과/실패 시: None 반환 (호출자가 fallback UI 제시)
    """
```

#### 저장 전략

- **경로**: `backend/uploads/agent_images/{builder_session_id}.png`
- **URL 형식**: `/api/builders/{builder_session_id}/image` (프록시 엔드포인트) 또는 직접 URL
- **선택 이유**: 
  - PoC 단계이므로 로컬 저장 (나중에 S3로 이전 용이)
  - Agent 생성 시 `agents.image_url` 컬럼으로 저장 (이 컬럼 이미 존재 확인됨)
  - Base64 URL보다 적은 메모리 사용

### 9. 라우터 엔드포인트 계약

#### 신규/변경 엔드포인트

**기존**:
- `GET /api/builder` — 세션 조회
- `GET /api/builder/{id}/stream` — SSE 스트림 시작 (현재)
- `POST /api/builder/{id}/confirm` — draft_config → Agent 생성

**변경**:
```
POST /api/builder
  Request: {"user_request": "..."}
  Response: {"session_id": "...", "user_id": "..."}
  → 세션 생성, StateGraph 자동 시작 (첫 메시지 SSE 스트림)

POST /api/builder/{id}/messages  (기존 /stream 대체)
  Request: {"user_request": "..."} (첫 메시지, 선택)
  Response: SSE (streaming.py 그대로)
  → StateGraph.astream() 호출, checkpointer 사용

POST /api/builder/{id}/messages/resume
  Request: {"response": ...}  (ask_user/approval/choice 응답)
  Response: SSE (streaming.py 그대로)
  → Command(resume=response) 전달, 중단 노드부터 재개

POST /api/builder/{id}/confirm
  Request: {} (draft_config 이미 Phase 7에 저장됨)
  Response: {"agent_id": "..."}
  → Phase 8 완료 후 agent_id 반환 (이미 생성됨)
```

### 10. 데이터 모델 (DB 변경 최소)

**기존 유지**:
- `builder_sessions` 테이블: status, draft_config 등 메타데이터 (변경 최소)
- `agents.image_url` 컬럼 이미 존재 (마이그레이션 불필요)

**신규 사용**:
- LangGraph checkpoint 테이블 (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`)
  - `thread_id = builder_session_id` 매핑
  - `get_checkpointer()` 함수 사용 (기존 채팅과 동일)

**Status 머신**:
```
[BUILDING] (세션 생성)
    ↓
[STREAMING] (Phase 1~7 진행 중)
    ↓
[PREVIEW] (Phase 7 도착, draft_config 저장)
    ↓
[CONFIRMING] (Phase 8 도착, 최종 승인 대기)
    ↓
[COMPLETED] (Agent 생성 완료)
    ↓
(또는 [FAILED] if error)
```

---

## 대안

### 대안 A: 기존 v2 유지 + HiTL만 추가

**장점**: 최소 변경  
**단점**:
- 순서 강제 불가능 (orchestrator.py의 7-phase 루프로는 LLM 제어 불가)
- SSE 이벤트 형식 통합 어려움 (별도 커스텀 스트림 필요)
- 이미지 생성 단계 추가 복잡 (orchist.py를 8-phase로 확장하고 또 다른 커스텀)

**판정**: 채택 안 함

### 대안 B: ReAct (create_deep_agent 이용)

**장점**: 기존 executor.py 재사용, HiTL 즉시 가능  
**단점**:
- LLM이 도구 선택 → Phase 순서 건너뛸 가능성
- mockup 이미지의 "엄격한 8-phase" 보장 불가
- 각 phase 내부 ask_user 루프도 LLM 결정 → 예측 불가

**판정**: 채택 안 함

### 대안 C: StateGraph (선택된 방안)

**장점**:
- 8-phase 순서를 그래프 토폴로지로 강제
- HiTL, checkpointer 완벽 지원
- SSE streaming.py 재사용 가능
- 각 노드 내부 approve 루프를 명시적 while로 제어

**단점**: 신규 구현 필요 (약 1200줄 추정)

**판정**: **채택됨** — 구조적 강건성과 UX가 우선

---

## 결과

### 구현 영향

| 범위 | 변경 | 이유 |
|------|------|------|
| **Backend** | `builder_v3/` 신규 모듈 1200줄 | StateGraph 8-phase 노드 + 라우터 통합 |
| **Frontend** | Tool UI 5개 신규 + `assistant-thread` 재사용 | mockup 이미지 UI 매칭 + HiTL 통합 |
| **Router** | `/messages`, `/messages/resume` 신규 | SSE + resume 엔드포인트 |
| **Service** | `run_build_stream()` 제거 → `graph.astream()` 대체 | 구조 단순화 |
| **DB** | 변경 없음 (또는 최소) | checkpoint 테이블만 신규 사용 |

### 폐기 대상

- `backend/app/agent_runtime/builder/orchestrator.py` (v2 파이프라인)
- `frontend/src/app/agents/new/conversational/_components/builder-thread.tsx`
- `frontend/src/app/agents/new/conversational/_components/phase-timeline.tsx`
- `frontend/src/lib/chat/use-builder-runtime.ts`
- `frontend/src/lib/sse/stream-builder.ts`

### 마이그레이션 경로

1. **Step 1**: `builder_v3/` 구현 완료 (테스트 포함)
2. **Step 2**: 라우터 교체 + `streaming.py` 호환성 검증
3. **Step 3**: 프론트엔드 Tool UI 구현
4. **Step 4**: 페이지 교체 및 회귀 테스트
5. **Step 5**: 기존 파일 폐기

---

## 인터페이스 계약

### BuilderState ↔ 노드

각 노드는 `BuilderState`를 입력으로 받아, 갱신된 state dict 또는 `Command(resume=...)`을 반환:

```python
async def phase_X(state: BuilderState) -> dict | Command:
    # 입력: 이전 노드의 state (모든 이전 결과 포함)
    # 출력: {"intent": {...}, "messages": [...]}
    #      또는 Command(resume=payload) — interrupt 처리 시
```

### 노드 ↔ SSE

노드가 `state["messages"].append(AIMessage(...))` 호출:
- `streaming.py`의 `stream_agent_response()` 함수가 자동으로 SSE 이벤트로 변환
- 기존 채팅 로직과 100% 동일

### 노드 ↔ Interrupt

노드가 `interrupt(payload)` 호출:
- LangGraph가 execution 일시 중단
- `streaming.py`가 `aget_state()` → `state.tasks[].interrupts[]` 추출 → SSE `interrupt` 이벤트 emit
- 프론트가 `HiTLContext.onResume()` → `/messages/resume` POST
- 백엔드가 `Command(resume=response)` 전달
- 같은 노드부터 재개

---

## 검증 전략

### 단위 테스트

```bash
# backend/tests/test_builder_v3_graph.py
# 각 노드 독립 테스트, mock state 사용
# 예: phase3_tools에 tools=None → tools 생성 확인
```

### 통합 테스트

```bash
# 그래프 도달 가능성: Phase 8 도달 시 1~7을 거쳤는가
# 각 interrupt 전후 state 일관성
# Phase 8 router → phase 3 재진입 → phase 8 재도착 검증
```

### E2E 검증 (브라우저)

1. **기본 흐름** (mockup 이미지 재현)
   - Phase 1~8 순차 진행
   - 매 phase 카드가 메시지 안에 누적 표시
2. **승인/수정 루프**
   - Phase 3에서 "수정 의견" 입력 → 같은 phase 재실행 → 새 추천 표시
3. **Phase 6 skip/generate**
   - "넘어가기" → image_url=None
   - "생성하기" → 이미지 생성 → 미리보기 → 확정/재생성/넘어가기
4. **Phase 8 router**
   - Phase 8에서 "도구 빼줘" 입력 → phase3_tools로 점프 → 재흐름
5. **기존 채팅 회귀**
   - `/agents/[id]/conversations/[cid]` 정상 동작

---

## 리스크 및 완화책

| 리스크 | 영향 | 완화책 |
|--------|------|--------|
| **Router 분류 오류** | Phase 8에서 잘못된 phase로 점프 | 구조화 출력(Pydantic enum) + ask_user fallback |
| **Interrupt resume idempotency** | 재진입 시 중복 LLM call | 노드 함수 설계 시 state 기반 조건부 실행 |
| **이미지 생성 timeout** | 60s 초과 → 사용자 경험 저하 | 폴백 UI ("다시" or "skip") + 비동기 후처리 고려 |
| **use-chat-runtime 추상화** | 기존 conversations 페이지 깨짐 | Step 3에서 회귀 테스트 필수 |
| **builder_session 상태 머신** | PREVIEW ↔ STREAMING 불일치 | Phase 7 진입 시 명시적 상태 전환 |
| **Checkpoint 저장소 부하** | 많은 phase 재시도 → DB 증가 | 세션 확인/정리 배치 job 고려 |

---

## 참고

- **기존 자산**: executor.py, streaming.py, ask_user.py, sub_agents/*.py 모두 그대로 재사용
- **인프라**: `get_checkpointer()`, `invoke_with_json_retry`, `stream_agent_response` 기존 함수 활용
- **계획**: `/Users/chester/.claude/plans/kind-squishing-shore.md`
- **Progress**: `/Users/chester/dev/natural-mold/progress.txt` (실시간 기술 결정 기록)
- **Mockup 이미지**: 계획 문서 이미지 1~4 참조 (8-phase, 진행 상황 카드, 승인 UI, 이미지 생성)

---

## ADR 승인 체크리스트

- [ ] 피차이: 기술 아키텍처 결정 검증 (자체)
- [ ] 젠슨: API 계약 검증 (`BuilderState` → 엔드포인트 매핑)
- [ ] 저커버그: 프론트엔드 Tool UI 계약 검증 (interrupt payload ↔ React 상태)
- [ ] 베조스: 테스트 전략 / 폐기 대상 정리

