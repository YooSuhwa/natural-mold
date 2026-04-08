# 삭제 분석 보고서 v2

> v2 Builder/Assistant 교체를 위한 기존 creation_agent, fix_agent 의존성 분석
> 분석일: 2026-04-07 | 분석자: bezos (QA)

---

## 즉시 삭제 가능

| 파일 | 이유 |
|------|------|
| `backend/app/agent_runtime/creation_agent.py` | v2 Builder 오케스트레이터로 완전 대체. 외부 의존성: `agent_creation_service.py`만 import → 동시에 교체 가능 |
| `backend/app/agent_runtime/fix_agent.py` | v2 Assistant로 완전 대체. 외부 의존성: `routers/fix_agent.py`만 import → 동시에 교체 가능 |
| `backend/app/schemas/fix_agent.py` | v2 `schemas/assistant.py`로 대체. 외부 의존성: `routers/fix_agent.py`만 import |
| `backend/tests/test_creation_agent.py` | 삭제 대상인 `creation_agent.py` 테스트. 10개 테스트 케이스 전부 대상 |
| `backend/tests/test_fix_agent.py` | 삭제 대상인 `fix_agent.py`와 `routers/fix_agent.py` 테스트. ~20개 테스트 케이스 전부 대상 |
| `frontend/tests/unit/api/creation-session.test.ts` | `creation-session.ts` API 클라이언트 테스트 |
| `frontend/tests/mocks/fixtures.ts` (부분) | `CreationMessageResult` 타입 import → 타입 삭제 시 해당 fixture도 제거/교체 필요 |

---

## 삭제 시 수정 필요 (의존성)

### Backend

| 삭제 대상 | 의존 파일 | 수정 내용 |
|-----------|----------|----------|
| `creation_agent.py` | `backend/app/services/agent_creation_service.py:8` | `from app.agent_runtime.creation_agent import run_creation_conversation` 삭제. `send_message()` 함수 (L39-73) 내부의 `run_creation_conversation()` 호출을 v2 Builder 호출로 교체 |
| `routers/agent_creation.py` | `backend/app/main.py:160,175` | `from app.routers import agent_creation` import 삭제, `app.include_router(agent_creation.router)` 삭제. v2 `routers/builder.py` 추가 |
| `routers/fix_agent.py` | `backend/app/main.py:163,176` | `from app.routers import fix_agent` import 삭제, `app.include_router(fix_agent.router)` 삭제. v2 `routers/assistant.py` 추가 |
| `creation_agent.py` | `backend/pyproject.toml:93` | `"app/agent_runtime/creation_agent.py" = ["E501"]` ruff 예외 설정 삭제 |
| `schemas/agent_creation.py` | `backend/app/routers/agent_creation.py:11-15` | 라우터 삭제 시 함께 삭제됨 (의존 단절) |
| `models/agent_creation_session.py` | `backend/app/models/__init__.py:2,24` | import 및 `__all__` export 삭제 (또는 v2 `BuilderSession` 모델로 교체) |
| `models/agent_creation_session.py` | `backend/app/models/user.py:26-28` | `creation_sessions` relationship 삭제 (또는 v2 builder_sessions로 교체) |
| `models/agent_creation_session.py` | `backend/app/services/agent_creation_service.py:10` | import 삭제 — 서비스 전체 교체 시 함께 처리 |
| `agent_creation_service.py` | `backend/app/routers/agent_creation.py:16` | 라우터 삭제 시 함께 삭제됨 |
| `test_agent_creation_extended.py` | 자체 (14 테스트) | `agent_creation_service`, `AgentCreationSession` 전면 참조. 전체 삭제 후 v2 테스트로 교체 |

### Frontend

| 삭제 대상 | 의존 파일 | 수정 내용 |
|-----------|----------|----------|
| `lib/api/creation-session.ts` | `app/agents/new/conversational/page.tsx:34` | `creationSessionApi`, `CreationMessageResult` import 삭제. v2 Builder API 클라이언트로 교체 |
| `lib/types/index.ts` (부분) | `creation-session.ts:2`, `conversational/page.tsx:35` | `CreationSession` (L295-302), `DraftConfig` (L304-311) 인터페이스 삭제. v2 Builder 타입으로 교체 |
| `components/agent/fix-agent-dialog.tsx` | `app/agents/[agentId]/settings/page.tsx:27,190` | `FixAgentDialog` import 및 렌더링 삭제. v2 Assistant 진입점으로 교체 |
| `app/agents/new/conversational/page.tsx` | `tests/pages/agent-conversational.test.tsx:2`, `agents/new/page.tsx:26` | 전체 페이지 삭제 또는 v2 Builder 페이지로 교체 |
| `tests/pages/agent-conversational.test.tsx` | 자체 | conversational 페이지 테스트. 전체 삭제 후 v2 Builder 페이지 테스트로 교체 |
| `tests/pages/dashboard.test.tsx` (부분) | L67-68 | `'대화로 만들기'` 링크 → `/agents/new/conversational` 경로 assertion 수정 필요 |
| `tests/pages/agents-new.test.tsx` (부분) | L27-30 | conversational option 경로 assertion 수정 필요 |
| E2E: `e2e/smoke.spec.ts` (부분) | L163-164 | settings 페이지의 'AI로 수정하기' 버튼 assertion → v2 Assistant 진입점으로 수정 |
| E2E: `e2e/smoke.spec.ts` (부분) | L357-389 | `Smoke Test - Conversational Creation` 테스트 블록 전체 → v2 Builder 페이지 테스트로 교체 |
| `messages/ko.json` (부분) | L52, L715 | `conversational` 관련 i18n 키 수정/교체 |

### DB/마이그레이션

| 대상 | 수정 내용 |
|------|----------|
| `agent_creation_sessions` 테이블 | **삭제하지 않음**. v2에서 `builder_sessions`로 확장/대체하는 마이그레이션 작성. 기존 데이터는 PoC이므로 drop+recreate도 가능하지만, Alembic 마이그레이션으로 추적 필요 |
| `alembic/versions/aa5b4cc59ddb_initial_tables.py` | 변경 불필요 (이미 적용된 마이그레이션). 새 마이그레이션에서 테이블 변경/대체 |

---

## 재사용 가능 로직 (v2로 이관)

| 함수/로직 | 위치 | v2에서 활용 방법 |
|-----------|------|-----------------|
| `confirm_creation()` 도구 이름 매칭 | `agent_creation_service.py:94-105` | Builder의 `build_final_agent` 단계에서 `recommended_tool_names` → Tool DB 레코드 자동 링크 로직 재사용. `func.lower(Tool.name).in_(lower_names)` 패턴 |
| `confirm_creation()` 스킬 이름 매칭 | `agent_creation_service.py:107-118` | Builder의 `build_final_agent`에서 동일하게 스킬 자동 링크 |
| `confirm_creation()` 모델 매칭 | `agent_creation_service.py:82-92` | `display_name` → Model ID 리졸브. Builder에서 재사용 |
| `confirm_creation()` Agent 생성 | `agent_creation_service.py:120-135` | Agent ORM 인스턴스 생성 + tool_links/skill_links 설정 패턴 |
| `_apply_changes()` 도구 추가/제거 | `routers/fix_agent.py:81-133` | Assistant의 도구 수정 기능에서 batch resolve 패턴 재사용. `func.lower(Tool.name).in_()` + 현재 tool_ids diff |
| `_apply_changes()` 모델 변경 | `routers/fix_agent.py:98-104` | Assistant의 모델 변경 도구에서 display_name → model_id 변환 재사용 |
| `extract_json_from_markdown()` | `message_utils.py` | **삭제 대상 아님**. 유틸리티로 v2에서도 계속 사용 |
| `strip_json_blocks()` | `message_utils.py` | **삭제 대상 아님**. 유틸리티로 v2에서도 계속 사용 |
| `convert_to_langchain_messages()` | `message_utils.py` | **삭제 대상 아님**. v2에서도 활용 가능 |

---

## 프론트엔드 영향 요약

### 삭제 파일 (6개)
1. `frontend/src/lib/api/creation-session.ts` — v2 Builder API 클라이언트로 교체
2. `frontend/src/components/agent/fix-agent-dialog.tsx` — v2 Assistant 진입 UI로 교체
3. `frontend/src/app/agents/new/conversational/page.tsx` — v2 Builder 페이지로 교체
4. `frontend/tests/unit/api/creation-session.test.ts` — v2 Builder API 테스트로 교체
5. `frontend/tests/pages/agent-conversational.test.tsx` — v2 Builder 페이지 테스트로 교체
6. `frontend/tests/mocks/fixtures.ts` (부분) — `CreationMessageResult` mock 제거

### 수정 파일 (6개)
1. `frontend/src/lib/types/index.ts` — `CreationSession`, `DraftConfig` 타입 삭제 → v2 Builder 타입 추가
2. `frontend/src/app/agents/[agentId]/settings/page.tsx` — `FixAgentDialog` import/렌더 삭제 → v2 Assistant 진입점
3. `frontend/src/app/agents/new/page.tsx:26` — `/agents/new/conversational` 라우팅 → v2 Builder 경로
4. `frontend/src/app/page.tsx:44-45` — 대시보드 '대화로 만들기' Quick Action 경로/라벨 수정
5. `frontend/src/components/layout/breadcrumb-nav.tsx:18` — `conversational` breadcrumb 키 수정
6. `frontend/messages/ko.json` — conversational, fix 관련 i18n 키 교체

### 수정 테스트 (3개)
1. `frontend/tests/pages/dashboard.test.tsx:67-68` — 경로 assertion
2. `frontend/tests/pages/agents-new.test.tsx:27-30` — conversational option assertion
3. `frontend/e2e/smoke.spec.ts:163,357-389` — fix agent 버튼 + conversational 페이지 E2E

---

## 삭제 순서 (의존성 기반)

v2 코드가 준비된 후, 다음 순서로 교체:

### Phase A: Backend (순서 중요)
1. 새 v2 파일 추가 (builder/, assistant/, 새 라우터/서비스/스키마)
2. `main.py`에 v2 라우터 등록
3. `main.py`에서 기존 라우터 제거 (`agent_creation`, `fix_agent`)
4. 기존 라우터 삭제: `routers/agent_creation.py`, `routers/fix_agent.py`
5. 기존 서비스 삭제: `services/agent_creation_service.py`
6. 기존 런타임 삭제: `agent_runtime/creation_agent.py`, `agent_runtime/fix_agent.py`
7. 기존 스키마 삭제: `schemas/agent_creation.py`, `schemas/fix_agent.py`
8. 모델 교체: `models/agent_creation_session.py` → `models/builder_session.py`
9. `models/__init__.py`, `models/user.py` 업데이트
10. `pyproject.toml` ruff 예외 삭제 (L93)
11. Alembic 마이그레이션 작성
12. 기존 테스트 삭제 + v2 테스트 추가

### Phase B: Frontend (Backend API 안정 후)
1. v2 API 클라이언트 + 타입 추가
2. v2 Builder 페이지, Assistant UI 추가
3. 기존 파일 삭제 (`creation-session.ts`, `fix-agent-dialog.tsx`, `conversational/page.tsx`)
4. 참조 수정 (settings, new, dashboard, breadcrumb, i18n)
5. 기존 테스트 삭제 + v2 테스트 추가
6. E2E 테스트 수정

---

## 영향 범위 요약

| 카테고리 | 삭제 | 수정 | 신규 (v2) |
|----------|------|------|----------|
| Backend 런타임 | 2 | 0 | ~4 (builder/*, assistant/*) |
| Backend 라우터 | 2 | 1 (main.py) | 2 (builder.py, assistant.py) |
| Backend 서비스 | 1 | 0 | 2 (builder_service, assistant_service) |
| Backend 스키마 | 2 | 0 | 2 (builder.py, assistant.py) |
| Backend 모델 | 1 (교체) | 2 (__init__, user) | 1 (builder_session) |
| Backend 설정 | 0 | 1 (pyproject.toml) | 0 |
| Backend 테스트 | 2 | 1 (extended) | ~2 |
| Frontend 페이지 | 1 | 3 | ~2 |
| Frontend 컴포넌트 | 1 | 0 | ~2 |
| Frontend API | 1 | 0 | 2 |
| Frontend 타입 | 0 | 1 | 0 (v2 타입 추가) |
| Frontend i18n | 0 | 1 | 0 |
| Frontend 테스트 | 2 | 3 | ~2 |
| E2E 테스트 | 0 | 1 | 0 |
| DB 마이그레이션 | 0 | 0 | 1 |
| **합계** | **15** | **14** | **~22** |
