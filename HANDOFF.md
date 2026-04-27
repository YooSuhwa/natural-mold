# HANDOFF — Builder v3 (8-phase StateGraph + Chat UI 통합)

**브랜치**: `feature/builder-v3-state-graph`
**Base**: `main @ e5876f2`
**최종 업데이트**: 2026-04-27
**Plan**: `/Users/chester/.claude/plans/kind-squishing-shore.md`

---

## 완료된 작업

- [x] **Builder v3 핵심**: LangGraph StateGraph 8-phase + HiTL + 채팅 UI 통합 (assistant-thread.tsx 재사용)
- [x] **8-phase 구조**: 1 init → 2 intent (ask_user) → 3/4/5 (approval+수정) → 6 image (skip/gen) → 7 save → 8 build (router 분기)
- [x] **신규 모듈**: `backend/app/agent_runtime/builder_v3/` (graph/state/todos/image_gen/nodes×9/constants), `frontend/src/components/chat/tool-ui/{phase-timeline,recommendation-approval,prompt-approval,image-generation,draft-config}-ui.tsx`
- [x] **API**: `POST /api/builder/{id}/messages`, `/messages/resume`, `GET /image/{filename}`
- [x] **Codex 리뷰 3라운드 + /simplify** 모든 지적 반영 (image_url 키 명시, "직접 입력" 제거, 빌드 실패 노출, interrupt_id 검증, ToolMessage close 패턴, Phase 2 revision 반영, ToolNames 상수, asyncio.gather, TOCTOU fix)

## 검증 결과

- **Backend**: pytest **671 passed** (회귀 0, +15 신규), ruff clean
- **Frontend**: pnpm build 성공, lint 0 error
- **수동 E2E**: 브라우저 검증 일부 (사용자 기존 세션에서 Phase 8까지 완료)

## 다음에 해야 할 작업

1. **커밋 + PR 생성** — 단일 PR로 모두 묶을지 plan의 4-PR 분리 권장 따를지 결정
2. **백엔드 v2 코드 제거 (별도 PR)**: `builder/orchestrator.py`, `services/builder_service.py::run_build_stream`, `routers/builder.py::stream_build` GET, `tests/test_builder_orchestrator.py`. 단 `builder/sub_agents/*.py` + `prompts/*.md`는 v3에서 import하므로 보존
3. **브라우저 E2E 시나리오 사용자 검증** — mockup 1~4 흐름 + 수정 요청 분기 + 이미지 skip/regenerate
4. **(선택) 후속 refactor**: Tool UI HiTL form 헬퍼 추출 (`useApprovalForm` hook + `ApprovalCard` wrapper, 5종 컴포넌트 ~40% 코드 감소 가능)
5. **(선택) frontend buildStreamState memoization** — SSE 이벤트 빈도 높을 때 re-render 비용 감소

## 주의사항

- **LangGraph checkpoint stale**: 같은 builder_session_id 새로고침해도 진행 상태 유지. 새 흐름 검증 시 새 user_request로 시작
- **stream_mode="messages"**: 노드 add_messages 결과도 stream으로 emit됨 (텍스트는 content_delta, ToolMessage는 tool_call_result). `builder:internal` tag로 sub-LLM 응답 필터링 필수
- **Tool UI 컴포넌트 mount 안정성**: `submitState` 컴포넌트 로컬 state. 페이지 새로고침 시 reset되므로 `close_pending_tool_card`로 result 채워서 status='complete' 강제 — wait 노드들이 이를 emit
- **conditional_edges + Command goto**: LangGraph 1.x에서 충돌 — 모든 wait/approval은 dict-only로 통일 (router만 Command + destinations)
- **이미지 src API_BASE prepend**: `/api/builder/.../image/...`는 backend(:8001) 직접 fetch 필요 → `lib/utils.ts::resolveImageUrl` 사용

## 관련 파일

핵심 신규:
- `backend/app/agent_runtime/builder_v3/graph.py` (8-phase + named routing fns)
- `backend/app/agent_runtime/builder_v3/state.py` (BuilderState TypedDict)
- `backend/app/agent_runtime/builder_v3/nodes/_helpers.py` (make_pending_tool_card, close_pending_tool_card, build_approval_result)
- `backend/app/agent_runtime/builder_v3/constants.py` (ToolNames)
- `backend/app/agent_runtime/builder_v3/image_gen.py` (OpenRouter Moldy)

수정 (보안/엣지):
- `backend/app/routers/builder.py` (BuilderResumeRequest interrupt_id, serve_builder_image ownership)
- `backend/app/services/builder_service.py` (run_v3_*, _transfer_builder_image_sync, asyncio.gather)
- `frontend/src/lib/chat/use-chat-runtime.ts` (resumeFn, lastInterruptIdRef, phase_timeline tool_name 매칭)
- `frontend/src/app/agents/new/conversational/page.tsx` (AssistantThread 기반 재작성)

문서: `tasks/deletion-analysis.md`, `docs/design-docs/builder-v3-architecture.md`

## 마지막 상태

- 브랜치: `feature/builder-v3-state-graph` (main에 머지 안 됨)
- working tree: 47 파일 변경 (+1300/-1560), uncommitted
- pytest: **671 passed**, ruff/lint clean
- backend dev server (uvicorn :8001) + frontend (next :3000) 실행 중

새 세션에서 "HANDOFF.md 읽고 커밋부터 진행해줘" 또는 "백엔드 v2 제거 PR 만들어줘" 등으로 이어가면 됩니다.
