# Deletion Analysis — Greenfield Credentials Rewrite

**DRI**: 베조스
**날짜**: 2026-04-29
**참조**: `PLAN.md`, `docs/design-docs/adr-009-greenfield-credentials.md`
**판정**: **GREEN** — 폐기 대상이 명확하고, 신규 코드와 분리되어 충돌 위험 낮음

---

## 1. 백엔드 폐기 대상

### 1.1 서비스 / 모델 / 라우터 (대량 폐기)

| 파일 | LOC | 폐기 사유 | 의존성 |
|---|---:|---|---|
| `backend/app/services/encryption.py` | ~80 | Fernet → Cipher V2 교체 | `app/security/cipher.py` 신규로 흡수 |
| `backend/app/services/credential_service.py` | ~250 | 신규 도메인으로 재작성 | M2에서 `app/credentials/` 도메인 모듈로 분산 |
| `backend/app/services/credential_registry.py` | ~120 | 신규 `CredentialRegistry`로 교체 | `app/credentials/registry.py` |
| `backend/app/services/connection_service.py` | ~350 | Connection 이원화 폐기 | Tool이 직접 credential_id FK 보유 |
| `backend/app/models/connection.py` | ~70 | Connection 테이블 폐기 | m13에서 DROP |
| `backend/app/routers/connections.py` | ~180 | Connection 라우터 폐기 | UI에서 Credentials 페이지로 통합 |
| `backend/app/seed/prebuilt_connections.py` | ~150 | env→Connection 시드 폐기 | `app/seed/bootstrap_from_env.py`로 교체 |
| `backend/scripts/google_oauth_setup.py` | ~100 | OAuth2 매뉴얼 스크립트 폐기 | OAuth2 라우터로 통합 |

**소계**: ~1,300 LOC 감소

### 1.2 Agent Runtime 도구 모듈 (재배선/이전)

| 파일 | LOC | 폐기 사유 |
|---|---:|---|
| `backend/app/agent_runtime/naver_tools.py` | ~150 | `app/tools/definitions/naver_search.py`로 이전 |
| `backend/app/agent_runtime/google_tools.py` | ~120 | `app/tools/definitions/google_search.py`로 이전 |
| `backend/app/agent_runtime/google_workspace_tools.py` | ~280 | `gmail_send/google_calendar_event/google_chat_message.py`로 분할 |
| `backend/app/agent_runtime/env_var_resolver.py` | ~90 | `app/credentials/interpolation.py`가 통합 처리 |

**소계**: ~640 LOC 감소

### 1.3 Agent Runtime 재배선 (수정, 폐기 아님)

| 파일 | 변경 내용 |
|---|---|
| `backend/app/services/chat_service.py` | `build_tools_config`, `get_agent_with_tools` 전면 재작성 |
| `backend/app/agent_runtime/executor.py` | import 정리 |
| `backend/app/agent_runtime/tool_factory.py` | `app/tools/runner.py` 위임 |
| `backend/app/agent_runtime/model_factory.py` | `agent.llm_credential` 복호화 경로 추가 |
| `backend/app/agent_runtime/trigger_executor.py` | 신규 chat_service 호출 + **L44-46 prefetch 누락 버그 동시 수정** |
| `backend/app/agent_runtime/creation_agent.py` | 의존성만 따라감 |
| `backend/app/agent_runtime/mcp_client.py` | `${credential.<field>}` 템플릿을 `app/credentials/interpolation.py` 위임 |

### 1.4 신규 파일 요약

- Security: 2 (`cipher`, `key_provider`)
- Credential 도메인: 7 + External Secrets 4 + 정의 11 = **22**
- Tools: 4 + 정의 6 = **10**
- MCP: 4
- Skills: 4
- 모델: 7
- 라우터: 4
- 시드: 1
- 마이그레이션: 1 (m13)
- 테스트: 9

**소계**: ~64 신규 파일

### 1.5 백엔드 LOC 변화 예상

- 폐기: −1,940
- 신규: +5,200 (도메인 + 테스트 포함)
- **순증**: +3,260

순증 사유: OAuth2 자동 refresh, External Secrets, 키 로테이션, audit log, 정의 카탈로그 11개, 도구 정의 6개 등 **신규 기능 도입**.

---

## 2. 프론트엔드 폐기 대상

| 파일/폴더 | 폐기 사유 |
|---|---|
| `frontend/src/app/connections/page.tsx` | Credentials로 통합 |
| `frontend/src/components/connection/` (전체) | 폐기 |
| `frontend/src/components/tool/` (옛 내용) | 신규로 재작성 |
| `frontend/src/components/skill/` (옛 내용) | 신규로 재작성 |
| `frontend/src/lib/api/connections.ts` | 폐기 |
| `frontend/src/lib/api/{tools,credentials,skills}.ts` (옛) | 신규로 재작성 |
| `frontend/src/lib/hooks/use-connections*.ts` | 폐기 |
| `frontend/src/lib/hooks/use-{tools,credentials,skills}*.ts` (옛) | 신규로 재작성 |

### 2.1 사이드바 변경

`frontend/src/components/layout/sidebar.tsx`:
- 제거: `Connections`
- 유지: `Agents`, `Tools`, `Skills`, `Credentials`, `Usage`
- 신규: `MCP Servers`

### 2.2 신규 프론트엔드 파일

- 페이지 4 (`credentials, mcp-servers, tools, skills`)
- 공통 컴포넌트 5 (`data-table, status-chip, icon, empty-state, dynamic-fields-form`)
- Credential/Tool/MCP/Skill 컴포넌트 = 4+4+3+3 = **14**
- API 4, Hooks 5, Types 4
- E2E 4

**소계**: ~40 신규 파일

---

## 3. DB 테이블 폐기 (m13에서 일괄)

| 테이블 | 처리 | 사유 |
|---|---|---|
| `connections` | DROP | Credential 단일화 |
| `credentials` | DROP + CREATE 신규 | `key_id`, `definition_key`, status enum |
| `credential_audit_logs` | CREATE 신규 | hook |
| `credential_defaults` | CREATE 신규 | scope별 default |
| `tools` | DROP + CREATE 신규 | `definition_key + credential_id` 단일 경로 |
| `agent_tools` | DROP + CREATE | tool FK 새 스키마 |
| `mcp_servers` | DROP + CREATE 신규 | credential_id FK + transport enum |
| `mcp_tools` | DROP + CREATE | discovery 캐시 |
| `skills` | DROP + CREATE 신규 | content_hash, package_metadata 등 |
| `agent_skills` | DROP + CREATE | skill FK 새 스키마 |
| `models` | DROP + CREATE | api_key_encrypted 컬럼 제거 |
| `llm_providers` | DROP | provider 레벨 키 폐기 |
| `agents` | ALTER ADD `llm_credential_id UUID FK credentials` | LLM 키도 Credential 통합 |

**downgrade**: `raise NotImplementedError("m13 is intentionally non-reversible — restore from backup")`

---

## 4. 영향받는 테스트

기존 테스트 중 폐기 대상 모듈을 직접 import하는 케이스:
- `tests/test_credentials.py` (기존) — 신규 스키마로 전면 재작성
- `tests/test_connections.py` — 폐기
- `tests/test_tools.py` (기존) — 신규 스키마로 재작성
- `tests/test_skills.py` (기존) — 신규 스키마로 재작성
- `tests/test_chat_service.py` (있다면) — `build_tools_config` 신규 시그니처 반영

기존 fixture (`mock_user`, `db_session`)는 변경 없음.

---

## 5. Scope Creep 후보 (별도 티켓)

본 PR에 포함하지 않음:

1. **interpolation.py 보안 강화 (sandbox)** — 단순 문자열 치환만 본 PR. sandbox는 후속.
2. **MCP discovery 캐시 TTL 정책** — 본 PR은 영구 저장만.
3. **OAuth2 PKCE** — 본 PR은 authorization code + body auth.
4. **Skills 패키지 검증/sandbox** — 본 PR은 메타 파싱만.
5. **Vault AppRole/JWT 인증** — 본 PR은 토큰 인증만.

**예외**: `trigger_executor.py L44-46 prefetch 누락 버그`는 M5에서 동시 수정 (재배선과 분리 불가).

---

## 6. 위험 평가

| 위험 | 확률 | 영향 | 대응 |
|---|:---:|:---:|---|
| 채팅/트리거 회귀 | 중 | 높음 | M5 후 즉시 회귀 시나리오 |
| n8n 식별자 누락 | 낮음 | 중 | `scripts/check_branding.py` CI 게이트 |
| OAuth refresh 동시성 | 중 | 중 | `SELECT ... FOR UPDATE` |
| Vault dev 환경 부재 | 낮음 | 낮음 | feature flag 기본 off |
| dev DB 데이터 소실 | 확정 | 낮음 (PoC) | README 명기 |
| 단일 PR 리뷰 부담 | 높음 | 중 | 마일스톤별 커밋 |
| 라이선스 적합성 | 낮음 | 중 | NOTICES.md + 변호사 1회 검토 |

---

## 7. 판정

**GREEN** — 진행 가능.

**근거**:
- 폐기 대상 명확, 의존 그래프 추적 완료
- 신규 파일 위주라 기존 코드 충돌 위험 낮음
- m13 단일 마이그레이션으로 DB 일관성 확보
- 브랜딩 검증 자동화 완료(M1 산출물)
- agent_runtime 재배선 경로가 chat_service 한 곳에 수렴

**조건**:
- M5 완료 후 채팅/트리거/MCP 통합 회귀 우선 검증
- m13 alembic upgrade는 사용자 확인 후 실행
- 머지 전 변호사 라이선스 검토 1회
