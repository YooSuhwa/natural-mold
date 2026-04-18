# 백로그 E — Connection 엔티티 통합 리팩토링 (실행 계획)

**상태**: M0 완료 (ADR-008 승인 대기) — M1 대기
**작성일**: 2026-04-18 (M0 보강: 2026-04-18)
**선행 조건**: 멀티 유저 인증 도입 이전에 완료 필요
**범위**: 백로그 E + F(CredentialPickerDialog 공통 셸) 통합
**ADR**: [`adr-008-connection-entity.md`](../../design-docs/adr-008-connection-entity.md)

---

## M0 합의 사항 (요약)

설계 인터뷰를 통해 다음 결정을 확정했다. 상세 근거는 ADR-008 참조.

| 항목 | 결정 |
|------|------|
| `provider_name` | VARCHAR 자유 문자열. PREBUILT는 `credential_registry` enum validator, MCP/CUSTOM은 자유 |
| UNIQUE 제약 | **없음**. UUID PK + user_id FK + `(user_id, type, provider_name)` 인덱스만 |
| `user_id` | NOT NULL FK (권한 분리의 기반) |
| env fallback | **유저 도구 실행 경로에서 제거**. 시스템 내부 기능(creation_agent, 이미지 생성)만 env 유지 |
| M3 이행 | mock user의 env 값 → credential → default connection 자동 시드 |
| `is_default` | 유저별 provider별 1개. `agent_tools.connection_id = NULL` → default 사용, 값 있으면 override |
| `extra_config` | MCP만 사용: `{url, auth_type, headers?, env_vars?, transport?, timeout?}`. PREBUILT/CUSTOM은 NULL |
| MCP `env_vars` | credential 필드 참조 템플릿(`${credential.xxx}`) 허용. M1은 스키마만, M2에서 해석 구현 |
| CUSTOM 공유 | 1 credential = 1 connection, 여러 도구가 N:1로 공유 |
| 롤백 | M2~M5 legacy 컬럼 read-only 유지 + Alembic downgrade 필수 |

---

---

## 1. 요구사항 요약

**목표**: MCP/PREBUILT/CUSTOM 도구의 credential 바인딩을 단일 `connections` 엔티티로 통일. 멀티 유저 인증 도입 전 선행 작업.

**해결 대상**:
1. PREBUILT 공유 행의 credential 뒤엉킴 (user A 연결이 user B 덮어씀)
2. 바인딩 위치 비일관 (MCP=서버 단위 / CUSTOM=도구 단위 / PREBUILT=공유 행)
3. 3개 auth 다이얼로그 중복 (백로그 F 흡수)

**비범위**:
- 멀티 유저 인증(로그인/세션) — E 완료 후 별도 작업
- 도구 실행 빌더(`build_naver_search_tool` 등)의 `auth_config` dict 인터페이스는 유지
- LangGraph PostgresSaver 체크포인트 구조 변경

---

## 2. 현재 구조 (탐색 결과 요약)

### Backend 해석 경로
```
chat_service.build_tools_config (chat_service.py:164-205)
  ├── MCP: mcp_servers.credential_id → resolve_credential_data → auth_config
  │       OR mcp_servers.auth_config (inline)
  │       (tool.credential_id는 MCP에서 무시 — PR #47)
  └── PREBUILT/CUSTOM: tool.credential_id → resolve_credential_data → auth_config
          OR tool.auth_config (inline)

agent_tools.config → merged_auth = {**cred_auth, **link.config}

executor._prepare_agent
  ├── PREBUILT → create_prebuilt_tool(name, auth_config) → build_*_tool
  ├── CUSTOM → create_tool_from_db(..., auth_config) → _build_http_tool_func
  └── MCP → _build_mcp_tools → _AuthInjectorInterceptor
```

### Provider 5종 (`credential_registry.py`)
- `naver` (api_key)
- `google_search` (api_key)
- `google_workspace` (oauth2)
- `google_chat` (api_key)
- `custom_api_key` (api_key)

### Frontend
- `CredentialSelect` / `CredentialFormDialog`: 이미 공용 컴포넌트 존재 (4곳 재사용)
- 3개 `*-auth-dialog.tsx`: 90% 동일 — 차이는 저장 endpoint와 provider 필터만
- `/connections` 페이지: Credential CRUD 중심 (Connection 개념 아직 없음)

---

## 3. 설계 방향

### Connection 엔티티 스키마
```sql
connections (신규)
  id                UUID  PK
  user_id           UUID  FK users (NOT NULL)
  type              VARCHAR(20)   -- 'prebuilt' | 'mcp' | 'custom'
  provider_name     VARCHAR(50)   -- naver, google_search, google_workspace, ...
  display_name      VARCHAR(200)  -- UI 표시명
  credential_id     UUID  FK credentials (nullable, ON DELETE SET NULL)
  extra_config      JSON  nullable   -- MCP: {url, auth_type}
  status            VARCHAR(20)   -- 'active' | 'disabled'
  created_at / updated_at
  UNIQUE (user_id, type, provider_name, display_name)
```

### 도구 타입별 연결
| 타입 | 해석 로직 |
|------|----------|
| **PREBUILT** | `tool.provider_name` + `current_user_id` → `connections` 조회 (per-user, per-provider). 공유 행 문제 해소 |
| **MCP** | `tool.connection_id` (1 connection = 1 MCP 서버, extra_config={url, auth_type}) |
| **CUSTOM** | `tool.connection_id` (현재 credential_id 경로를 connection 간접화) |

### `mcp_servers` 처리
Connection으로 흡수 후 drop. 데이터는 마이그레이션으로 이관 (type='mcp', extra_config={url, auth_type}).

### `agent_tools` override
- `agent_tools.connection_id` (optional FK) 추가 — 특정 에이전트가 기본 connection 대신 다른 것을 사용 가능
- 기존 `agent_tools.config` inline override는 M6에서 폐기

### UI 통합 (F 흡수)
3개 `*-auth-dialog.tsx` → 공통 `ConnectionBindingDialog` + context prop (`{type, provider}`). 저장 endpoint는 통합 connection API 경유.

### env 기반 fallback 유지
`(auth_config or {}).get("naver_client_id") or settings.naver_client_id` 패턴은 보존. Connection이 없을 때 서버 env 기본값 사용.

---

## 4. 마일스톤 (6 PR)

> 단일 PR 불가 규모. 각 마일스톤이 **독립 배포 가능** + **리뷰 가능 크기**. 마일스톤 사이에는 legacy fallback을 유지해 언제든 배포 가능한 상태 유지.

### **M0: ADR + 상세 스펙** (docs PR, 단일 세션 가능)
- `docs/design-docs/adr-008-connection-entity.md` 작성
  - 맥락/결정/대안/결과 4 섹션
  - 스키마 확정, 해석 로직, 이관 전략
- 이 exec-plan 문서 업데이트 (세부 사항 보강)
- 테스트 시나리오 목록 (회귀 + 신규)
- **산출물**: ADR + exec-plan 확정판

### **M1: Connection 테이블 + CRUD API** (backend PR, 단일 세션)
- Alembic `m8_add_connections` — 테이블 + 인덱스 + UNIQUE
- `app/models/connection.py` 신규
- `app/schemas/connection.py` — CreateConnection, UpdateConnection, ConnectionResponse
- `app/services/connection_service.py` — CRUD + credential resolution 헬퍼
- `app/routers/connections.py` — `GET/POST/PATCH/DELETE /api/connections`
- `mcp_servers` 테이블 유지 (parallel run)
- **아직 쓰이지 않음** → 기존 시스템 영향 0
- 신규 테스트: `tests/test_connections.py`
- **완료 기준**: 전체 pytest 통과, 기존 기능 회귀 0

### **M2: MCP → Connection 이관** (backend PR, TTH 권장)
- Alembic `m9_migrate_mcp_to_connections` — 각 `mcp_servers` row → `connections` row (type='mcp', extra_config={url, auth_type})
- `tools.connection_id` 컬럼 추가 (nullable FK)
- `mcp_servers` 데이터 → `connections`로 복사 + `tools.mcp_server_id` 기준 `tools.connection_id` 매핑
- `chat_service.build_tools_config` MCP 분기를 connection 경유로 재작성
- `mcp_servers` 테이블을 deprecate (read-only, 아직 drop 안 함)
- `test_mcp_connection`, `test_tools_router_extended` 회귀 검증
- **완료 기준**: MCP 도구 실행 경로 전부 connection 경유, 기존 MCP 테스트 통과

### **M3: PREBUILT per-user Connection** (backend + 프론트 일부)
- Backend: PREBUILT 해석 로직 변경
  ```python
  if tool.type == PREBUILT:
      conn = get_connection(user_id, provider_name=tool.provider_name, type='prebuilt')
      cred_auth = resolve_credential_data(conn.credential) if conn else {}
  ```
- `tools.credential_id`는 PREBUILT에서 무시 (legacy fallback 유지는 M6까지)
- env var fallback 경로 유지 (`settings.naver_*`)
- Frontend: `/connections` 페이지에서 PREBUILT connection 생성 지원 (provider 드롭다운)
- **완료 기준**: PREBUILT 도구를 여러 유저가 각자의 connection으로 실행 가능 (mock user 다중 ID 테스트)

### **M4: CUSTOM Connection 통합** (backend + 프론트 일부)
- Backend: CUSTOM 도구도 `tool.connection_id` 경유
- Alembic `m10_migrate_custom_credentials` — 기존 `tool.credential_id`가 있는 CUSTOM 도구 → connection 생성 후 FK 설정
- `tools.credential_id`는 이 시점부터 deprecated (drop은 M6)
- Frontend: `add-tool-dialog.tsx` Custom 탭이 connection 생성하도록 재배선
- **완료 기준**: CUSTOM 도구 전체 실행 경로가 connection 경유

### **M5: UI 통합 + F 흡수** (frontend PR)
- 신규 `components/connection/ConnectionBindingDialog.tsx` — 공통 셸
- `prebuilt-auth-dialog.tsx` / `custom-auth-dialog.tsx` / `mcp-server-auth-dialog.tsx` 교체
- `add-tool-dialog.tsx` MCP/Custom 탭 재배선 (connection 생성)
- `/connections` 페이지 재편: Credential 중심 → Connection 중심 (Credential은 하위 보조)
- `agent_tools.connection_id` override UI (에이전트 설정 화면)
- 3 다이얼로그 중복 제거 확인 (F 완료 처리)

### **M6: Cleanup** (backend + 프론트)
- Alembic `m11_drop_legacy_columns`:
  - `mcp_servers` 테이블 drop
  - `tools.credential_id` drop
  - `tools.auth_config` drop (inline 필드)
  - `tools.mcp_server_id` drop
  - `agent_tools.config` drop (inline override)
- legacy fallback 코드 제거 (credential_service의 `resolve_server_auth`, `tool.credential_id` 분기)
- 타입/주석 정리 (`lib/types/index.ts`에서 deprecated 필드 제거)
- HANDOFF.md 업데이트 — E 완료, 다음 작업 = 멀티 유저 인증

---

## 5. 수정 파일 요약

### Backend
| 파일 | 영향 마일스톤 |
|------|--------------|
| `app/models/connection.py` | M1 신규 |
| `app/models/tool.py` | M2(connection_id 추가), M6(legacy drop) |
| `app/models/mcp_server.py` | M2(deprecate), M6(drop) |
| `app/models/agent.py` (agent_tools) | M5(connection_id), M6(config drop) |
| `app/services/connection_service.py` | M1 신규 |
| `app/services/chat_service.py:164-205` | M2/M3/M4 분기별 수정, M6 정리 |
| `app/services/credential_service.py` | M6 (`resolve_server_auth` 제거) |
| `app/routers/connections.py` | M1 신규 |
| `app/routers/tools.py` | M6 auth_config 라우트 정리 |
| `alembic/versions/*` | M1(m8) / M2(m9) / M3(m10 PREBUILT seed 정리) / M4(m10?) / M6(m11 drop) |
| `app/seed/default_tools.py` | M3 provider_name 정리 |
| `tests/test_connections.py` | M1 신규 |
| 기존 tool/mcp 테스트 | M2~M4 회귀 갱신 |

### Frontend
| 파일 | 영향 마일스톤 |
|------|--------------|
| `components/connection/ConnectionBindingDialog.tsx` | M5 신규 |
| `components/tool/prebuilt-auth-dialog.tsx` | M5 교체 |
| `components/tool/custom-auth-dialog.tsx` | M5 교체 |
| `components/tool/mcp-server-auth-dialog.tsx` | M5 교체 |
| `components/tool/add-tool-dialog.tsx` | M5 재배선 |
| `app/connections/page.tsx` | M3 PREBUILT UI, M5 Connection 중심 재편 |
| `lib/api/connections.ts` | M1 신규 |
| `lib/hooks/use-connections.ts` | M1 신규 |
| `lib/types/index.ts` | M1 Connection 타입, M2 Tool.connection_id, M6 legacy 제거 |

---

## 6. 위험 요소

| 위험 | 완화책 |
|------|--------|
| **데이터 이관 중 이중 상태** (M1~M5 기간 `mcp_servers` + `connections` 공존) | M2에서 단방향 sync, `mcp_servers`는 read-only deprecate. M6에서 drop |
| **PREBUILT env fallback 깨짐** (`settings.naver_*`) | M3에서 connection 없을 때 env fallback 경로 유지 + 테스트 필수 |
| **agent_tools.config override 시맨틱 변경** | M5에서 `connection_id` override 도입 시 기존 `link.config` 데이터 일회성 마이그레이션. M6 전까지 inline도 수용 |
| **PoC mock user 전제와 멀티 유저 전제 혼재** | 각 마일스톤 테스트에서 user_id 복수 케이스로 검증 (mock user 여러 개). 실제 멀티 유저는 후속 PR |
| **M1 통과 후 M6까지 긴 기간 프로덕션 배포 중** | 각 마일스톤이 독립 배포 가능하도록 설계 — legacy fallback 유지 |
| **MCP 서버 테이블 drop 시 영향 범위** | M6 전 전체 테스트 + prod DB 스냅샷 + 롤백 마이그레이션 검증 |
| **프론트/백 타입 어긋남** | M1~M4 각 PR에서 `lib/types/index.ts` 동기 갱신 필수 (PR 체크리스트) |
| **credential_registry와 connection.provider_name 불일치** | M0 ADR에서 enum 정합성 명시, M1부터 validator로 enforce |

---

## 7. 검증 전략

**마일스톤별 공통**:
```bash
cd backend
uv run ruff check .
uv run pytest
uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head

cd ../frontend
pnpm lint && pnpm build
```

**마일스톤별 추가**:
- M1: 신규 test_connections.py (CRUD, IDOR)
- M2: MCP 도구 실행 스모크 + mcp_server_id→connection_id 매핑 검증
- M3: 두 개의 mock user로 같은 PREBUILT 도구 실행 — 각자 다른 credential 사용 확인
- M4: CUSTOM 도구 실행 회귀
- M5: 3 다이얼로그 대체 후 각 워크플로 E2E (agent-browser)
- M6: 전체 통합 회귀 + Alembic 양방향 왕복

---

## 8. 진행 전략 제안

- **M0**: 단일 세션에서 완료 가능 (문서만). ADR 합의 중요.
- **M1~M4**: 각 마일스톤 = 1 PR = 1 worktree + 1 세션 권장. TTH 또는 단독 구현 둘 다 가능.
- **M5**: 프론트 중심 — `frontend` 에이전트 또는 단독.
- **M6**: Cleanup — 작지만 회귀 위험 크므로 QA에 집중.

각 마일스톤 완료 시 HANDOFF.md 갱신 + 다음 마일스톤 문서 링크.

---

## 9. 체크리스트 (모든 PR 공통)

- [ ] Alembic 마이그레이션 상하 왕복 PASS
- [ ] `backend/tests/` 신규 + 회귀 전체 PASS
- [ ] `frontend/` lint + build PASS
- [ ] 프론트/백 타입 동기화
- [ ] HANDOFF.md 진행 상태 반영
- [ ] ADR-008 상태 업데이트 (필요 시)
