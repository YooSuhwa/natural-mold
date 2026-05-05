# CHECKPOINT — HiTL Phase 1: 표준 미들웨어 인스턴스화 (사용자 무영향)

> 참조: `docs/design-docs/adr-012-hitl-middleware-migration.md` §Phase 1, `HANDOFF.md`
> 브랜치: `feature/hitl-phase1-middleware-instance` (main HEAD `750d587` 에서 분기)
> done-when: 표준 미들웨어가 deep agent 에 주입되지만 SSE wire 는 자체 형식 유지 (dual-path 그대로)

---

## 핵심 사실 (코드 정독 결과)

- `deepagents.create_deep_agent(interrupt_on=...)` 는 이미 `HumanInTheLoopMiddleware` 를 **자동 주입**한다 (deepagents/graph.py docstring: "HumanInTheLoopMiddleware (if interrupt_on is provided)")
- 즉 현재 코드 (`executor.py:537-548`) 도 표준 미들웨어를 사용 중 — 다만 **인스턴스를 명시적으로 받아 middleware list 에 합치는 경로가 아닌, deepagents 의 자동 주입에 의존** 중
- `middleware_registry.py:419` 의 `human_in_the_loop` 제외 목록은 **자동 주입과의 중복 회피**가 목적. 자동 주입과 명시 주입은 둘 중 하나만 가능

## Phase 1 의 정확한 목표

ADR-012 의 "표준 미들웨어가 deep agent 에 주입되지만 SSE wire 는 자체 형식 유지" = 이미 90% 달성. 남은 격차:

1. **트리거(invoke) 모드 강제 차단** — `execute_agent_invoke` 경로에서 `interrupt_on` 이 도달하면 hang. 명시적으로 `None` override 필요. (현재는 `_WRITE_TOOL_KEYWORDS` 자동 추출이 트리거 모드에서도 작동 가능)
2. **명시적 인스턴스 경로** — `HumanInTheLoopMiddleware(interrupt_on=...)` 인스턴스를 `build_middleware_instances` 결과와 동일한 list 에 합쳐 deep agent 에 전달. `create_deep_agent` 의 `interrupt_on` 파라미터는 `None`. 이로써 **registry 단일 경로 + description_prefix 등 추가 옵션 제어 가능 + 디버깅 추적 용이**
3. **middleware_registry 정리** — `human_in_the_loop` 을 `DEEPAGENT_BUILTIN_TYPES` 에서 제외하지 않고, `build_middleware_instances` 가 정상적으로 인스턴스화. 단, 사용자 `middleware_configs` 에서 `human_in_the_loop` 가 들어와도 executor 가 별도 경로로 처리 (자동 추출 vs 사용자 명시 둘 다 지원)

## M1: 코드 변경 (피차이 단독 DRI)

- [x] `backend/app/agent_runtime/middleware_registry.py`: `human_in_the_loop` 을 `DEEPAGENT_BUILTIN_TYPES` 에서 **유지**(중복 자동 주입 방지) 하되, executor 가 명시 인스턴스화하므로 자동 주입 경로 비활성화. 주석 갱신.
- [x] `backend/app/agent_runtime/executor.py:472-548`:
  - [x] `interrupt_on` 추출 로직 그대로 유지
  - [x] `HumanInTheLoopMiddleware` 인스턴스 생성 (`interrupt_on` 이 None 이 아닐 때만)
  - [x] 인스턴스를 `middleware` list 에 append
  - [x] `build_agent(..., interrupt_on=None, ...)` — deepagents 자동 주입 비활성화
  - [x] `execute_agent_invoke` 경로(`include_ask_user=False`) 에서 `interrupt_on` 강제 `None` (트리거 모드 자동 승인)
- 검증: `cd backend && uv run ruff check . && uv run pyright app/agent_runtime/executor.py app/agent_runtime/middleware_registry.py`
- done-when: ruff/pyright clean
- 상태: **done**

## M2: 회귀 가드 테스트 (피차이 단독 DRI)

- [x] `backend/tests/test_hitl_middleware.py` 신규 (~330 라인):
  - [x] `test_hitl_middleware_instance_injected_when_interrupt_on_provided` — `executor._build_*` 헬퍼 또는 `build_runtime` 호출 결과의 미들웨어 list 에 `HumanInTheLoopMiddleware` 인스턴스 존재 확인
  - [x] `test_hitl_middleware_not_injected_in_trigger_mode` — `include_ask_user=False` 경로에서 미들웨어 list 에 없음 + `interrupt_on=None`
  - [x] `test_hitl_middleware_per_tool_policy_applied` — `interrupt_on` dict 가 인스턴스에 그대로 전달
  - [x] `test_hitl_middleware_auto_extraction_from_write_keywords` — 명시 dict 없을 때 자동 추출 동작
  - [x] `test_deepagents_interrupt_on_param_is_none_when_explicit_instance` — `create_deep_agent` 자동 주입과의 중복 회피
- 검증: `cd backend && uv run pytest tests/test_hitl_middleware.py -v`
- done-when: 신규 테스트 모두 PASS
- 상태: **done** (5/5 PASS)

## M3: 통합 게이트 (베조스 DRI)

- [ ] `cd backend && uv run alembic upgrade head` — 마이그레이션 무영향 확인
- [ ] `cd backend && uv run ruff check .` — clean
- [ ] `cd backend && uv run pytest tests/` — 기존 821 + 신규 ~5 = 826 PASS, 회귀 0
- [ ] `cd backend && uv run pyright app/ tests/` — 0 error
- [ ] 사용자 영향 없음 검증 — SSE wire format 변경 X (`streaming.py` 미수정, `routers/conversations.py` 미수정)
- 검증: 전체 게이트 4종
- done-when: 모두 PASS, 회귀 0
- 상태: pending

## 수정 금지 (Phase 2+ 대상)

- `backend/app/agent_runtime/tools/ask_user.py`
- `backend/app/agent_runtime/streaming.py`
- `backend/app/routers/conversations.py`
- `backend/app/agent_runtime/builder_v3/**`
- `backend/app/schemas/conversation.py`
- `frontend/**`

## PR

단일 PR — backend 코드 + 테스트만. 사용자 무영향 (SSE wire 자체 형식 유지).
