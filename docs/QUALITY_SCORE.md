# Quality Score — Moldy Agent Builder

> 최종 검증일: 2026-04-17
> 검증자: bezos (QA Engineer)

---

## 백로그 C — credentials list N+1 복호화 제거 (2026-04-17)

### 게이트

| 게이트 | 결과 | 비고 |
|--------|------|------|
| `uv run ruff check .` | PASS | 0 errors |
| `uv run pytest tests/test_credentials.py -v` | PASS | 5/5 신규 |
| `uv run pytest` | PASS | **545 passed** (540+ 기준 초과) |
| `alembic upgrade head ↔ downgrade -1 ↔ upgrade head` | PASS | 젠슨 S2 왕복 확인 |

### 신규/변경 파일

| 파일 | 변경 |
|------|------|
| `backend/app/models/credential.py` | `field_keys: Mapped[list[str] \| None]` 컬럼 추가 |
| `backend/alembic/versions/m7_add_credential_field_keys.py` | 신규 마이그레이션 + 백필 |
| `backend/app/services/credential_service.py` | create/update 동기화, extract 캐시 우선 |
| `backend/tests/test_credentials.py` | 신규 5 시나리오 |

### 삭제 분석 (M1)

- 실제 삭제: **0건** (스코프 엄격 준수)
- 단순화 제안: 1건 (별도 티켓 이관)
- 보류: 3건 (is_active/has_data/fallback — 스코프 외 또는 의도적 보존)
- 산출물: `tasks/deletion-analysis-c.md`

### 판정

**GO** — 모든 M0~M4 PASS. M5 (통합/커밋)은 사티아 DRI.

---

## v2 Builder/Assistant 프로젝트 — 최종 빌드 검증 (2026-04-07)

### 빌드/린트 게이트

| 게이트 | 결과 | 비고 |
|--------|------|------|
| `uv run ruff check .` | PASS | 0 errors |
| `uv run pytest` | PASS | 284 passed, 0 failed (6.93s) |
| `pnpm build` | PASS | TypeScript 3.2s, 13 static + 5 dynamic pages, 0 errors |
| `pnpm lint` (ESLint) | PASS | 0 errors, 0 warnings |

### 테스트 커버리지 변화

| 시점 | 테스트 수 | 비고 |
|------|-----------|------|
| M1 (구현 전) | 332 | 기존 creation_agent, fix_agent 테스트 포함 |
| 최종 (구현 후) | 284 | 기존 테스트 48개 삭제 (v1 코드 제거) |
| **v2 신규 테스트** | **0** | Builder/Assistant 유닛 테스트 미작성 |

### v2 신규 파일 (Backend)

| 카테고리 | 파일 | 상태 |
|----------|------|------|
| Builder 오케스트레이터 | `agent_runtime/builder/orchestrator.py` | EXISTS |
| Builder 서브에이전트 | `builder/sub_agents/intent_analyzer.py` | EXISTS |
| Builder 서브에이전트 | `builder/sub_agents/tool_recommender.py` | EXISTS |
| Builder 서브에이전트 | `builder/sub_agents/middleware_recommender.py` | EXISTS |
| Builder 서브에이전트 | `builder/sub_agents/prompt_generator.py` | EXISTS |
| Assistant 에이전트 | `agent_runtime/assistant/assistant_agent.py` | EXISTS |
| Assistant 도구 | `assistant/tools/read_tools.py` | EXISTS |
| Assistant 도구 | `assistant/tools/write_tools.py` | EXISTS |
| Assistant 도구 | `assistant/tools/clarify_tools.py` | EXISTS |
| Builder 라우터 | `routers/builder.py` | EXISTS |
| Assistant 라우터 | `routers/assistant.py` | EXISTS |
| Builder 서비스 | `services/builder_service.py` | EXISTS |
| Assistant 서비스 | `services/assistant_service.py` | EXISTS |
| Builder 스키마 | `schemas/builder.py` | EXISTS |
| Assistant 스키마 | `schemas/assistant.py` | EXISTS |
| Builder 모델 | `models/builder_session.py` | EXISTS |

### v2 신규 파일 (Frontend)

| 카테고리 | 파일 | 상태 |
|----------|------|------|
| Builder API | `lib/api/builder.ts` | EXISTS |
| Assistant API | `lib/api/assistant.ts` | EXISTS |
| Assistant 패널 | `components/agent/assistant-panel.tsx` | EXISTS |

### 삭제 파일 (Backend) — 7/7 확인

| 파일 | 상태 |
|------|------|
| `agent_runtime/creation_agent.py` | DELETED |
| `agent_runtime/fix_agent.py` | DELETED |
| `routers/agent_creation.py` | DELETED |
| `routers/fix_agent.py` | DELETED |
| `services/agent_creation_service.py` | DELETED |
| `schemas/agent_creation.py` | DELETED |
| `schemas/fix_agent.py` | DELETED |

### 삭제 테스트 (Backend) — 3/3 확인

| 파일 | 상태 |
|------|------|
| `tests/test_creation_agent.py` | DELETED |
| `tests/test_fix_agent.py` | DELETED |
| `tests/test_agent_creation_extended.py` | DELETED |

### main.py 라우터 교체

| 이전 | 이후 | 상태 |
|------|------|------|
| `agent_creation.router` | `builder.router` | PASS |
| `fix_agent.router` | `assistant.router` | PASS |

### models/__init__.py 교체

| 이전 | 이후 | 상태 |
|------|------|------|
| `AgentCreationSession` | `BuilderSession` | PASS |

---

## 미해결 이슈 (3건)

### ISSUE-1: 죽은 코드 — Frontend 삭제 누락 (심각도: LOW)

| 파일 | 상태 | 영향 |
|------|------|------|
| `frontend/src/lib/api/creation-session.ts` | 파일 존재, 어디서도 import 안 됨 | 빌드 영향 없음, tree-shaking |
| `frontend/src/components/agent/fix-agent-dialog.tsx` | 파일 존재, 어디서도 import 안 됨 | 빌드 영향 없음, tree-shaking |

빌드/런타임에 영향 없지만 코드베이스 위생상 삭제 권장.

### ISSUE-2: 죽은 코드 — Backend 모델 파일 잔존 (심각도: LOW)

| 파일 | 상태 | 영향 |
|------|------|------|
| `backend/app/models/agent_creation_session.py` | 파일 존재, `__init__.py`에서 import 안 됨 | 빌드 영향 없음 |

`BuilderSession`으로 교체 완료되었으나 구 파일 삭제 누락. Alembic 마이그레이션 고려 후 삭제 권장.

### ISSUE-3: v2 유닛 테스트 부재 (심각도: MEDIUM)

Builder 오케스트레이터, Assistant 에이전트, v2 라우터/서비스에 대한 유닛 테스트가 없음.
- 기존 48개 테스트 삭제됨 (v1 코드 제거)
- v2 신규 테스트 0개
- **테스트 커버리지 갭**: Builder 7단계 파이프라인, Assistant 도구 호출, SSE 스트리밍

---

## 이전: M1 빌드 검증 (2026-04-07)

| 게이트 | 결과 | 비고 |
|--------|------|------|
| `pnpm build` | PASS | TypeScript 3.1s |
| `pnpm lint` | PASS | 0 errors |
| `uv run pytest` | PASS | 332 passed |
| `uv run ruff check .` | FAIL | 2 errors (I001) — 이후 수정 완료 |

---

## 이전: UI/UX 개선 프로젝트 (2026-04-07)

### 라우트 완결성 (14/14)

모든 라우트 PASS.

### UI/UX 기능 검증 (10/10)

모든 항목 PASS.

---

## 총평

**v2 최종 판정: CONDITIONAL GO**

PASS:
- Backend ruff: 0 errors
- Backend pytest: 284 passed
- Frontend build: 0 errors (TypeScript + 18 pages)
- Frontend lint: 0 errors
- 기존 코드 삭제: 7/7 backend 파일 삭제 완료
- v2 신규 코드: 16 backend + 3 frontend 파일 존재 확인
- main.py 라우터 교체 완료
- models/__init__.py 교체 완료 (AgentCreationSession -> BuilderSession)

조건부 이슈:
- **ISSUE-1** (LOW): Frontend 죽은 코드 2개 — 삭제 권장
- **ISSUE-2** (LOW): Backend 모델 파일 1개 잔존 — 삭제 권장
- **ISSUE-3** (MEDIUM): v2 유닛 테스트 0개 — 커버리지 갭

**GO 조건**: ISSUE-3 (v2 테스트)은 별도 태스크로 후속 처리 가능. ISSUE-1, 2는 코드 위생 이슈로 즉시 삭제 가능.
빌드/린트/기존 테스트 모두 통과하므로 **GO** 판정.
