# Quality Score — Moldy Agent Builder

> 최종 검증일: 2026-05-09
> 검증자: bezos (QA Engineer)

---

## ADR-016 Multi-user Auth (S2~S7) — 2026-05-09

### 게이트

| 게이트 | 결과 | 비고 |
|---|---|---|
| `uv run ruff check app/ tests/` | PASS | 0 errors |
| `uv run pytest` | PASS | **947 passed, 3 xfailed (BUG escalations), 2 deselected** |
| `pnpm lint` | PASS | 0 errors |
| `pnpm build` | PASS | 모든 라우트 빌드 성공 (Proxy middleware 포함) |
| Mock user 흔적 grep (backend/app/, frontend/src/) | PASS | 0건 |
| `alembic upgrade head` | DEFERRED | dev DB는 head 상태. 운영 DB는 별도 마이그레이션 윈도우 |

### Authentication 도메인 등급

| 영역 | 등급 | 비고 |
|---|:---:|---|
| 백엔드 인증 코어 (`auth/`) | A | JWT(access/refresh/csrf) 분리 type, bcrypt cost 12, refresh hash 저장 |
| 라우터 audit (`/api/auth`) | A | register/login/refresh/logout/me 5 엔드포인트, rate-limit, CSRF exempt 분리 |
| Service 레이어 owner filter | A | 모든 owner-scoped query에 `Agent.user_id == user_id` predicate |
| Super_user 가드 | A | 6 엔드포인트(system-credentials × 5, models × 3) 모두 PASS |
| Multi-user isolation | A | 격리 매트릭스 10/10, enumeration oracle 통일 (404) 검증 |
| User cleanup / cascade | A | LangGraph thread + refresh + agent CASCADE + system 보존 8/8 PASS |
| CSRF double-submit | A | 7/7 PASS (header≠cookie, sub mismatch, garbage 모두 거부) |
| **Refresh replay defense** | **B-** | 감지·logging은 작동하나 mass-revoke가 commit 누락으로 미적용 — escalation 2 |
| **Login lockout** | **B-** | 동일 클래스 commit 누락 — failed_login_attempts 카운터 영구 0, escalation 1 |
| 프론트엔드 인증 흐름 | A | 19 신규 + 5 수정 파일, 빌드 PASS, proxy.ts middleware 정상 |
| 보안 체크리스트 | A- | OWASP Top 10 8/10 PASS, 2건은 운영자 설정 의존(cookie_secure, JWT_SECRET) + escalation 2건 |
| 마이그레이션 m36 | A | refresh_tokens, users 컬럼, FK CASCADE, ADR 번호 보정 정상 |

### 변경 통계 (S2~S6 누적)

- 백엔드 신규 파일: 8 (auth/* 4, models/refresh_token, routers/auth, schemas/auth, services/auth_service, services/user_service)
- 백엔드 신규 테스트: 6 (test_auth_register, test_auth_login, test_auth_refresh, test_csrf, test_multiuser_isolation, test_user_cleanup) — **40 PASS + 3 xfail**
- 프론트엔드 신규: 19 + 5 수정 (auth pages, login form, useAuth hook, proxy middleware, 등)
- 마이그레이션: 1 (m36_multiuser_auth)

### Escalation (deploy 차단 사항)

1. **CRITICAL: Login failure counter 미커밋** — `auth_service.authenticate` 실패 path가 commit 없이 raise → `failed_login_attempts` 영구 0, lockout 무력화. brute-force 무제한 가능.
2. **CRITICAL: Refresh replay mass-revoke 미커밋** — `rotate_refresh` replay 감지 후 `_revoke_all_active` UPDATE가 raise 전에 rollback → 도난 refresh가 victim 세션을 강제 무효화하지 못함.

두 escalation 모두 동일 클래스 버그(라우터 commit 경계 누락)로 단일 PR에서 일괄 fix 가능. `tasks/security-checklist-multiuser-auth.md` ESCALATION 섹션 참조. 수정 후 `tests/test_auth_login.py`와 `tests/test_auth_refresh.py`의 `xfail strict` 데코레이터 제거 필요.

### 운영자 deploy 직전 액션

1. `JWT_SECRET` 32 byte 랜덤 환경변수 설정 (미설정 시 ephemeral 키)
2. `COOKIE_SECURE=true` + `COOKIE_DOMAIN` 명시
3. 위 escalation 2건 머지 + `xfail` 제거 검증
4. 첫 운영자 가입 직후 `ALLOW_FIRST_USER_AS_ADMIN=false`
5. `main.py`의 CORS `allow_origins`를 환경변수 기반으로 교체 (현재 dev origin 하드코딩)

### 판정

**CONDITIONAL GO** — 격리 매트릭스 + super_user 가드 + cleanup은 production-ready. 그러나 **2건의 commit 누락 버그가 보안 핵심 방어를 무력화**하므로 escalation fix 머지 전에는 production deploy 금지.

---

## Greenfield Credentials Rewrite (M0~M6) — 2026-04-29

### 게이트

| 게이트 | 결과 | 비고 |
|---|---|---|
| `python scripts/check_branding.py` | PASS | 금지 식별자 0건, 금지 npm scope 0건, 자산 블랙리스트 0건 |
| `uv run ruff check .` | PASS | 0 errors |
| `uv run pytest tests/` | PASS | **480 passed**, 1 deselected, 1 warning (TestRequestSpec collection 무해) |
| `pnpm lint` | PASS | 0 errors, 1 informational warning (react-hooks/incompatible-library — TanStack Table) |
| `pnpm build` | PASS | 16 routes (credentials, mcp-servers, tools, skills 신규) |
| `alembic upgrade head` | DEFERRED | 사용자 확인 후 실행 (data-loss 액션) |
| Playwright E2E | DEFERRED | 4 specs 작성, 백엔드 실행 필요 — 사용자 실행 |

### 도메인별 등급

| 도메인 | 등급 | 비고 |
|---|:---:|---|
| Cipher V2 (security/) | A | 23 tests, moldy-encryption-v1, key_id 멀티키 검증 완료 |
| Credential 도메인 (credentials/) | A | 16 tests + OAuth2 + Tester + Vault, GenericAuth + interpolation + audit log |
| Tools 도메인 (tools/) | A | 12 도구 정의, ToolDefinition 단일 경로, GenericAuth 통일 |
| MCP (mcp/) | B+ | 디스커버리 + OAuth, agent_mcp_servers 링크 테이블 미구현 (후속) |
| Skills (skills/) | A | text/package 양방향, zip-slip + symlink 방어, content_hash |
| agent_runtime 재배선 | A | chat_service 단일 경로, prefetch 버그 수정, 480 회귀 PASS |
| 키 로테이션 cron | A | rotate_credentials_to_active_key 잡 + audit log rotate |
| External Secrets (Vault) | B | HVAC SDK 실구현, KV v2, AppRole/JWT 미지원(후속) |
| 마이그레이션 m18 | A- | DROP+CREATE+ALTER, downgrade NotImplementedError, dialect-aware. 실제 PostgreSQL upgrade 미실행 |
| 프론트엔드 디자인 시스템 | A | DataTable + dynamic-fields-form 일관 적용 |
| 프론트엔드 페이지 (4) | A | credentials/tools/mcp-servers/skills 동작, build PASS |
| 브랜딩/라이선스 가드 | A | CI 게이트 강제 |

### 변경 통계

- 백엔드 신규 파일: ~64 (security 2 + credentials 22 + tools 10 + mcp 4 + skills 4 + models 7 + routers 4 + seed 1 + alembic 1 + tests 10)
- 백엔드 폐기: 21 prod + 21 tests
- 프론트엔드 신규: ~29 (디자인 4 + 페이지 4 + 컴포넌트 14 + types/api/hooks 15 + e2e 4)
- 프론트엔드 폐기: ~24
- 신규 테스트: ~110 (cipher 23 + branding 1 + credentials 16 + oauth2 + tester + external_secrets + tools + mcp + skills 21 + seed 5 + migration 5 + chat_integration 3 + rotation 2)

### 후속 (별도 PR/티켓)

1. `alembic upgrade head` 실제 PostgreSQL 실행 — 사용자 확인 필요 (dev DB 폐기)
2. Playwright E2E 라이브 API 실행 — 백엔드 기동 후
3. 변호사 라이선스 검토 1회 — 외부 배포 시
4. agent_mcp_servers 링크 테이블 도입 — MCP 도구를 에이전트에 직접 연결
5. OAuth2 callback state Redis/DB 백킹 — 멀티프로세스 배포 시
6. TestRequestSpec → CredentialTestSpec rename — pytest collection 경고 제거
7. Vault AppRole/JWT 인증 추가
8. interpolation sandbox 강화 (보안 표면)

### 판정

**GO** — 단일 PR로 머지 가능. 6개 마일스톤 모두 게이트 PASS. 회귀 위험 Low (480 tests 단일 사이클 PASS).

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

---

## Marketplace Resources Phase 1 (M1~M9) — 2026-05-19

### 도메인별 등급

| 도메인 | 등급 | 근거 |
|--------|------|------|
| **Marketplace catalog / read API** | **A** | Slice A read-only endpoints + visibility 매트릭스 (super_user/owner/ACL/unrelated × private/restricted/public/unlisted/system) 검증. 25 access tests + 12 listing tests + 11 migration tests + 15 regression tests. enumeration oracle envelope 동등성 가드. |
| **Marketplace install** | **A** | 8 install tests + 7 E2E scenarios (모두 PASS). OPEN-1 (install_service lazy load) 2026-05-19 RESOLVED — `select(...).options(selectinload(MarketplaceItem.acl_entries))`로 eager-load. Phase 1 출시 게이트 #1 (enumeration oracle 방지) 가드 통과. strict xfail 자동 감지 → 베조스 promote 완료. |
| **Marketplace publish + secret scan** | **A** | 8 publish integration tests + 53 secret_scan unit tests. 파일 패턴 9개 + 내용 패턴 6개 (OI-4 `\bsk-…{20,}\b` boundary 검증). 256KB cap + binary skip + symlink skip 가드. |
| **Credential system (ADR-007/009 재사용 + 신규 8개)** | **A** | 13 기존 + 8 신규 k-skill definitions (총 21개). 10 credential injection tests: fail-fast 409, mapped-only env, override priority (`agent_skills.config.credential_bindings`), ownership drift silent missing. Cipher V2 round-trip 회귀 가드. |
| **Runtime mount (per-thread)** | **A** | 10 isolation tests. `build_skill_runtime_context(cfg, data_dir)` per-thread `copytree(symlinks=False)` 격리. selected-skill mount (`ctx.descriptors`이 보안 경계). Cross-thread prefix-spoof 가드. `cleanup_stale_runtime_roots` mtime 기반 retention. |
| **Redaction (multi-channel)** | **A** | 16 redaction tests. `redact_credential_values` (literal value, `len<5` 가드, 길이 정렬), `redact_keys` (recursive structural mask), subprocess stdout/stderr, SSE TOOL_CALL_START.parameters, exception detail 모두 통합. `streaming.py` 호출 지점 pin. |
| **k-skill importer (CLI)** | **B+** | super_user CLI 전용. 모듈 존재 + admin status endpoint mount 가드. 실제 upstream sync는 운영 환경 검증 필요. 단위 테스트는 jensen 트랙. |
| **Frontend Marketplace UI** | **Pending** | M8 진행 중 (M8a 디자인 스펙 in-progress, M8b 미완료). 빌드/lint 검증 후 재평가. |

### Phase 1 출시 게이트 (PRD §13) 검증 결과

8개 게이트 통합 검증: `backend/tests/test_marketplace_phase1_gates.py` (22 tests).

| Gate | 상태 | 책임 |
|------|------|------|
| 1. Access control | ✅ PASS | `marketplace.access` 술어 + 라우터 enumeration oracle |
| 2. Secret safety | ✅ PASS | `secret_scan` 9 파일 + 6 내용 패턴 + redaction 통합 |
| 3. Runtime isolation | ✅ PASS | per-thread root + selected-skill mount + retention |
| 4. Credential runtime | ✅ PASS | fail-fast 409 + mapped-only env + override 우선 |
| 5. k-skill sync | ✅ PASS (skip 가능) | admin endpoint mount 가드, 실제 sync는 CLI/운영 |
| 6. Backward compatibility | ✅ PASS | Skill ORM legacy columns 보존 + to_runtime_dict 키셋 |
| 7. Listing 승인 | ✅ PASS | `_base_catalog_query` default `public+published+is_listed` 가드 |
| 8. ADR-016 정합 | ✅ PASS | 모든 mutation route `verify_csrf` + `get_current_user`/`require_super_user` |

### 검증 명령

```bash
cd backend
uv run pytest tests/test_marketplace_phase1_gates.py -v   # 22 PASS
uv run pytest tests/test_marketplace_e2e.py -v            # 7 PASS (xfail 해제 후)
uv run pytest                                              # 전체 1191 PASS, 0 xfailed, 회귀 0
uv run ruff check .                                        # clean
```

### Closed Issues

| ID | Severity | Status | Resolution |
|----|----------|--------|------------|
| **OPEN-1** | MEDIUM | ✅ RESOLVED 2026-05-19 | `install_service.install_item`을 `select(...).options(selectinload(acl_entries))` 로 교체 (젠슨). strict xfail이 XPASS로 자동 감지 → 베조스 promote. test_marketplace_e2e.py::TestScenario_10_4_RestrictedACL는 이제 canonical regression guard. |

### Open Issues

| ID | Severity | Description | Owner |
|----|----------|-------------|-------|
| **OPEN-2** | LOW | M8 (Frontend Marketplace UI) 진행 중. Spec 정합성은 M8b 완료 후 재평가. | 저커버그 |
| **OPEN-3** | LOW | k-skill importer 실제 upstream sync는 단위 테스트 범위 외. 운영 환경에서 dry-run 후 실제 sync 1회 수행 필요. | 운영 |

### GO/NO-GO 판정

**Backend 트랙: ✅ FULL GO** (2026-05-19) — 8개 출시 게이트 모두 통과 + OPEN-1 해소. Frontend 트랙은 M8b 완료 시점에 재평가.

**근거**:
- 36 보안 critical 테스트 (runtime isolation 10 + credential injection 10 + redaction 16) PASS
- 53 secret_scan unit tests PASS
- 25 access matrix tests + 12 listing tests + 11 migration tests + 15 regression tests PASS
- **7 E2E user scenarios (PRD §10.1~10.7) 모두 PASS** (xfail strict 자동 감지 → 젠슨 fix → 베조스 promote)
- 22 Phase 1 출시 게이트 통합 검증 PASS
- 회귀 0, ruff 0

---

## ADR-019 System LLM Settings — S5 통합검증 (2026-05-26, 베조스)

### GO/NO-GO 판정: ✅ FULL GO (fast-follow closed 2026-05-26)

| 게이트 | 결과 | 근거 |
|--------|------|------|
| S5-1 backend ruff + pytest | ✅ PASS | `ruff check .` clean, `pytest` **1219 passed**, 2 deselected, 0 회귀 (fast-follow +1) |
| S5-2 frontend build + lint | ✅ PASS | `pnpm build` 성공(`/settings/system-llm` 라우트 생성), `pnpm lint` clean |
| S5-3 HIGH#1 super_user 가드 | ✅ CLOSED | `test_get/put_requires_super_user`(403), `test_invalid_credential_detail_is_byte_identical`(404↔422 detail byte-identical) |
| S5-4 HIGH#2 FK SET NULL | ✅ CLOSED | `test_credential_delete_sets_slot_null`(국소 engine+PRAGMA, conftest 무수정) PASS. 베조스 false-pass 반증: PRAGMA 제거 시 credential_id NULL 안 됨 입증 → load-bearing 회귀가드 |
| S5-5 핵심 시나리오 | ✅ PASS | `test_assistant_stream_surfaces_unconfigured`(SSE `event:error` code=`system_model_not_configured`), image base_url payload우선/canonical/raise 3케이스, assistant `create_chat_model(...,base_url)` 전달 |

신규 테스트 검증: `test_system_llm_settings.py` **19 PASS** (S2 11 + 베조스 리뷰 하드닝 8). 모든 신규 assertion 실질적(거짓통과 없음) 확인.

### Open Items

| ID | 심각도 | 설명 | 담당 |
|----|--------|------|------|
| ~~**ADR019-OPEN-1**~~ | ✅ RESOLVED | (2026-05-26) `test_credential_delete_sets_slot_null` 추가 — 국소 engine+PRAGMA, conftest 무수정. 베조스 false-pass 반증으로 load-bearing 확인. 1219 PASS. | 젠슨 |
| **ADR019-OPEN-2** | LOW | 전역 aiosqlite `PRAGMA foreign_keys=ON` 채택 — 1218 테스트 회귀확인 필요. builder_session 등 타 FK SET NULL 테스트에도 잠재 영향. **별도 follow-up 이슈** | 젠슨/운영 |
| **ADR019-OPEN-3** | LOW | 머지 후 운영자가 3슬롯 미설정 시 Builder/Assistant/이미지 동작 불가(ADR 의도). 배포 노트 "운영자 설정 필수" 명시 필요 | 운영 |

**근거**: 전 게이트 그린(backend **1219 PASS**/0 회귀, frontend build+lint clean), HIGH#1·#2 모두 CLOSED, 핵심 시나리오(미설정 SSE surface + base_url passthrough) verified. fast-follow FK SET NULL 회귀가드 머지 전 닫힘 → **FULL GO**.
