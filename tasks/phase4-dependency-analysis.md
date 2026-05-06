# Phase 4 — ask_user 의존성 매핑 (베조스 DRI)

> 일시: 2026-05-06
> 목적: ask_user 도구 retire (옵션 B) 전 의존성 100% 파악 → 회귀 위험 최소화
> 결론: ask_user는 **두 경로**에서 사용. 메인 채팅(retire) vs builder_v3(보존) 분리 필요.

## 1. 핵심 정의 & 등록 (메인 채팅)

| 위치 | 내용 | 결정 |
|------|------|------|
| `backend/app/agent_runtime/tools/ask_user.py:1-36` | 함수 정의 (LangGraph interrupt 호출) | ✅ 삭제 |
| `backend/app/agent_runtime/executor.py:34` | `from .tools.ask_user import ask_user as ask_user_tool` | ✅ 삭제 |
| `backend/app/agent_runtime/executor.py:439-443` | `include_ask_user: bool = True` 파라미터 | ✅ 삭제 (모든 호출처) |
| `backend/app/agent_runtime/executor.py:502-504` | `if not include_ask_user: interrupt_on = None` (트리거 차단) | ✅ 삭제 |
| `backend/app/agent_runtime/executor.py:506-509` | `interrupt_on.setdefault("ask_user", {"allowed_decisions": ["respond"]})` | ✅ 삭제 |
| `backend/app/agent_runtime/executor.py:557-558` | `if include_ask_user: langchain_tools.append(ask_user_tool)` | ✅ 삭제 |

## 2. 스트리밍 어댑터 (보존 — builder_v3 의존성)

| 위치 | 내용 | 결정 |
|------|------|------|
| `backend/app/agent_runtime/streaming.py:97-117` | `if intr_value.get("type") == "ask_user"` 분기 | 🔒 보존 — builder_v3 native interrupt 처리 |
| `backend/app/agent_runtime/streaming.py:84-86` | 주석: "자체 ask_user.py native interrupt ... fallback 안전망" | 🔄 주석 갱신 ("builder_v3 native interrupt 어댑터") |
| `backend/app/agent_runtime/streaming.py:387` | 주석: "ask_user.py 어댑터 포함" | 🔄 주석 갱신 |

## 3. Builder v3 (보존 — ADR-012 Phase 5 영역)

| 위치 | 내용 | 결정 |
|------|------|------|
| `builder_v3/__init__.py:4` | docstring | 🔒 보존 |
| `builder_v3/constants.py:13` | `ASK_USER = "ask_user"` 상수 | 🔒 보존 |
| `builder_v3/state.py:75` | 주석 | 🔒 보존 |
| `builder_v3/graph.py:72` | 주석 | 🔒 보존 |
| `builder_v3/nodes/phase2_intent.py:4-10, 111, 162-215` | Phase 2 intent ask_user pending card + interrupt 발행 | 🔒 보존 |
| `builder_v3/nodes/router.py:4, 77-90` | router fallback ask_user interrupt | 🔒 보존 |

## 4. Frontend (보존 — builder_v3 의존)

| 위치 | 내용 | 결정 |
|------|------|------|
| `frontend/src/components/chat/tool-ui/user-input-ui.tsx:174` | `toolName: 'ask_user'` 컴포넌트 등록 | 🔒 보존 (코드) + 🔄 JSDoc "Builder v3 전용" |
| `frontend/src/lib/chat/tool-ui-registry.ts:54` | 주석 | 🔄 주석 갱신 ("메인 채팅 retired, builder_v3 전용") |
| `frontend/src/lib/chat/use-chat-runtime.ts:100, 430-432` | 주석/처리 | 🔄 주석 갱신 |
| `frontend/messages/ko.json:558-571` | `userInput` 라벨 | 🔒 보존 (builder_v3 사용) |

## 5. API 계약 (보존 — builder_v3 영역)

| 위치 | 내용 | 결정 |
|------|------|------|
| `backend/app/routers/builder.py:39` | BuilderResumeRequest docstring | 🔒 보존 |

## 6. 테스트

| 파일 | 라인 | 시나리오 | 결정 |
|------|------|---------|------|
| `tests/test_executor.py:125-129` | "Only ask_user tool should be present (auto-included)" | ✅ 갱신 (도구 0개 어설션) |
| `tests/test_executor.py:185` | "2 user tools + ask_user auto-injected" → 2 user tools만 | ✅ 갱신 |
| `tests/test_executor.py:235, 272, 344` | ask_user count assertion | ✅ 갱신 |
| `tests/test_executor.py:551-575` | `test_ask_user_not_included_in_invoke` | ✅ 삭제 (도구 자체 없음) |
| `tests/test_hitl_middleware.py:117` | `include_ask_user=False` 호출 | ✅ 갱신 (파라미터 제거) |
| `tests/test_builder_v3.py:177-230` | `test_phase2_ask_user_resume` | 🔒 보존 |
| `tests/test_hitl_wire.py:280-295` | `test_ask_user_native_adapted_to_respond_action` | 🔄 시나리오 명확화 — "builder_v3 native interrupt → respond" |
| `tests/test_hitl_wire.py:337-350` | `test_ask_user_native_emits_adapted_standard_chunk` | 🔄 시나리오 명확화 |

## 7. 회귀 리스크 맵

| 리스크 | 영향 | 가드 |
|--------|------|------|
| **streaming.py 어댑터 삭제** | builder_v3 native interrupt → 표준 chunk 변환 실패 | 🔒 보존 결정 + M5 가드 테스트 |
| **interrupt_on 자동 등록 삭제 후 메인 채팅 ask_user 호출** | 도구 자체 없으므로 LLM이 호출 불가 (0 tool definition) | M5 가드 — 도구 미주입 검증 |
| **frontend UserInputUI 삭제** | builder_v3 UI 렌더 실패 | 🔒 보존 결정 |
| **ko.json 라벨 삭제** | builder_v3 UI 라벨 누락 | 🔒 보존 결정 |
| **prompt 언급** | system_prompt에 ask_user 직접 명시 안 됨 (executor.py:530-548 스킬 규칙 검토 필요) | 피차이 ADR §Phase 4 갱신 시 확인 |

## 8. 핵심 인사이트

1. **옵션 B retire = 메인 채팅 도구 한정**. builder_v3는 ADR-012 §Phase 5 영역으로 자체 native interrupt 패턴 유지.
2. **streaming.py 어댑터는 builder_v3가 native interrupt 발행하므로 필수**. Phase 4에서 제거 X (HANDOFF가 "어댑터 분기 제거" 언급한 것은 옵션 A 가정).
3. **Frontend UI 변경 거의 없음** — 주석/JSDoc만 갱신.
4. **회귀 위험 최소** — 도구 자체를 제거하면 LLM이 호출 불가능하고, 표준 미들웨어가 다른 도구를 모두 처리하므로 ask_user 의존 시나리오는 빌더 한정.
