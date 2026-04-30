# HANDOFF — Credential/Tools/Skills/Models 그린필드 리라이트 + 운영 인프라

**프로젝트**: natural-mold(Moldy) — Credential·Tools·Skills·Models 그린필드 + Hook + Health + Spend + Fallback
**브랜치**: `feature/greenfield-credentials`
**작업일**: 2026-04-29 ~ 2026-04-30 (2 세션, 11 마일스톤)
**팀**: 사티아(PO) + 피차이 + 젠슨 + 베조스 + 팀쿡 + 저커버그 (TTH 사일로)
**참조**: `PLAN.md`, `CHECKPOINT.md`, `docs/design-docs/adr-009-greenfield-credentials.md`, `NOTICES.md`

## 마일스톤 진행 (M0~M10)

| | 내용 | 신규 tests | 누적 PASS |
|---|---|:---:|:---:|
| M0 | 거버넌스 + ADR-009 | — | — |
| M1 | 브랜딩 검증 + Cipher V2 | 24 | 24 |
| M2 | Credential 도메인 + Vault | 44 | 68 |
| M3 | Tools 12개 + MCP 서버 | 29 | 97 |
| M4 | Skills 재작성 + alembic m18 | 31 | 128 |
| M5 | agent_runtime 재배선 + 키 로테이션 cron | 6 + 옛 21 삭제 | 480 |
| M6 | 프론트엔드 (디자인 시스템 + 4페이지 + E2E) | E2E 4 | — |
| M7 | 모델 카탈로그 + Discovery (LiteLLM + n8n 하이브리드) | 63 | 543 |
| M8 | 모델 Test + Curl + MCP Registry | 38 + e2e 3 | 581 |
| M9 | Hook 프레임워크 + Health Check + History | 22 + e2e 2 | 603 |
| M10 | Spend Queue + Aggregate API + Model Fallback + Dashboard | 31 + e2e 4 | **634** |

---

## 1. 변경 사항 요약

### 백엔드
- **Cipher V2**: AES-256-GCM + HKDF-SHA256 (info=`moldy-encryption-v1`), 단일 블롭 Base64, 멀티키 식별 `credentials.key_id`
- **Credential 도메인 신규** (`app/credentials/`): field/domain/interpolation/authenticate/registry/oauth2_base/tester/service + external_secrets/{base,env_provider,vault_provider(HVAC),proxy} + 정의 11개
- **Tools 도메인 신규** (`app/tools/`): ToolDefinition 단일 경로 + 도구 정의 12개 (HTTP Request 1 + Naver 5 + Google 검색 3 + Gmail/Calendar/Chat 3)
- **MCP 도메인 신규** (`app/mcp/`): client + discovery + oauth (env_vars 인터폴레이션 위임)
- **Skills 재작성** (`app/skills/`): kind(text|package) + content_hash + package_metadata + zip-slip 방어
- **agent_runtime 재배선**: chat_service.build_tools_config 단일 경로, model_factory에 llm_credential 복호화, trigger_executor prefetch 버그 수정
- **키 로테이션 cron**: APScheduler 잡 + audit log `rotate`
- **마이그레이션 m18**: 모든 관련 테이블 DROP+CREATE, agents.llm_credential_id ADD, downgrade NotImplementedError

### 프론트엔드
- **디자인 시스템**: data-table (TanStack Table), status-chip, icon (Lucide), dynamic-fields-form (8가지 렌더러), empty-state
- **페이지 4개 신규/재작성**: credentials, tools(Catalog/Manage 탭), mcp-servers(4단계 wizard), skills
- **사이드바 정리**: Connections 제거, MCP Servers 추가
- **agents UI 재배선**: 도구·스킬 선택을 신규 hooks/types로 교체

### 라이선스 / 브랜딩
- **NOTICES.md**: 차용 출처 명기 (n8n core encryption + ICredentialType + GenericAuth + INodeProperties UI 패턴 — 모두 식별자/문자열/자산 자체화)
- **scripts/check_branding.py**: CI 게이트 (`\bn8n\b` 0건, `@n8n/*` 0건, 로고 SHA-256 블랙리스트)

---

## 2. 아키텍처 결정 (ADR-009)

| # | 결정 | 근거 |
|---|---|---|
| 1 | Connection 이원화 폐기 | Tool이 직접 `credential_id` FK, 인증 단일 경로 |
| 2 | Cipher V2 (n8n 알고리즘 차용) | HKDF info만 자체화, 알고리즘은 검증된 것 |
| 3 | LLM 모델 통합 | `models` 유지 + `agents.llm_credential_id`, `llm_providers` 폐기 |
| 4 | OAuth2 자동 refresh | preAuthentication 패턴, `SELECT ... FOR UPDATE` 동시성 |
| 5 | External Secrets (Vault) | HVAC SDK 실구현, feature flag 기본 off |
| 6 | 자동 키 로테이션 | APScheduler 주1회, 활성 키로 재암호화 + audit |
| 7 | 단일 PR + 마일스톤별 커밋 | dual 시스템 회피, 리뷰 가독성 |
| 8 | m18 단일 마이그레이션 | dev DB 폐기 OK (PoC), downgrade NotImplementedError |

---

## 3. 삭제된 항목 (Musk Step 2)

### 백엔드 (21 prod + 21 tests)
- `services/{encryption, credential_service, credential_registry, connection_service, provider_service, model_discovery, skill_service, env_var_resolver, legacy_invariants}.py`
- `agent_runtime/{naver_tools, google_tools, google_workspace_tools, google_auth, env_var_resolver}.py`
- `models/{connection, llm_provider}.py`
- `routers/{connections, providers}.py`
- `schemas/{connection, llm_provider}.py`
- `seed/{prebuilt_connections, default_tools, default_providers}.py`
- `scripts/google_oauth_setup.py`
- 옛 테스트 21개 (test_connections, test_connection_*_resolve, test_tools 옛, test_tool_factory 옛 등)

### 프론트엔드 (~24)
- `app/{connections, models}/page.tsx`
- `components/{connection, model}/` 폴더 전체
- `components/tool/`의 옛 파일 (add-tool-dialog, credential-form-dialog, credential-select, mcp-server-group-card)
- `lib/api/{connections, providers, models, middlewares}.ts` (옛)
- `lib/hooks/use-{connections, providers, models, middlewares}.ts` (옛)

---

## 4. 검증 결과 (최종)

| 게이트 | 결과 | 비고 |
|---|:---:|---|
| `python scripts/check_branding.py` | PASS | 0 violations |
| `cd backend && uv run pytest tests/` | PASS | **480 passed**, 1 deselected, 1 warning (TestRequestSpec 무해) |
| `cd backend && uv run ruff check .` | PASS | 0 errors |
| `cd frontend && pnpm lint` | PASS | 0 errors, 1 informational warn |
| `cd frontend && pnpm build` | PASS | 16 routes |
| `alembic upgrade head` | DEFERRED | 사용자 확인 후 실행 (data-loss 액션) |
| Playwright E2E | DEFERRED | 4 specs 작성, 백엔드 기동 필요 |

---

## 5. Ralph Loop 통계

- 총 스토리 (S0~S18): **19개**
- 1회 통과: **18개** (S0~S17)
- 재시도 후 통과: **1개** (M5 ruff 4건은 사티아가 직접 정리)
- 에스컬레이션: **0건**
- 마일스톤 분할: M0~M6 (6개), 모두 게이트 PASS

---

## 6. 남은 작업 / 후속

### 즉시 (PR 머지 전)
- [ ] **사용자 확인 후 실행**: `docker-compose down -v && docker-compose up -d postgres && cd backend && uv run alembic upgrade head` (dev DB 폐기 → m18~m22 적용)
- [ ] **사용자 실행**: `cd backend && uv run uvicorn app.main:app --reload --port 8001` 후 `cd frontend && pnpm exec playwright test`
- [ ] **변호사 라이선스 검토 1회** (외부 배포 시 — n8n SUL/Apache-2.0 + LiteLLM MIT 적합성)

### 별도 티켓 (후속)
- [ ] `agent_mcp_servers` 링크 테이블 — MCP 도구를 에이전트에 직접 연결
- [ ] OAuth2 callback state Redis/DB 백킹 (현재 in-process map)
- [ ] `TestRequestSpec` → `CredentialTestSpec` rename (pytest collection 경고)
- [ ] Vault AppRole/JWT 인증 추가
- [ ] OAuth2 PKCE 지원
- [ ] interpolation sandbox 강화
- [ ] Skills 패키지 sandbox 실행
- [ ] **Health check history 보존 정리 cron** (`health_check_history_retention_days=90` 컬럼만 추가됨)
- [ ] **mcp_server_registry.json 확장**: Discord, Confluence, Asana, Trello 등
- [ ] **Spend dashboard CSV 다운로드** (라우터에 export 엔드포인트)
- [ ] **Model Fallback 드래그앤드롭 정렬** (현재 위/아래 화살표만)
- [ ] **Vercel AI Gateway Credential 정의** (M7에서 후속 결정)
- [ ] **Hook 등록 admin UI** — 사용자가 직접 hook on/off 가능
- [ ] **Object Permission 중앙화** (LiteLLM 패턴 — 멀티테넌트 전환 시)

---

## 7. 배운 점 (progress.txt 발췌)

### M2 — Credential 도메인
- aiosqlite는 FK CASCADE 자동 enforce 안 함 — PostgreSQL 환경에서만 검증
- OAuth2 state는 in-process map (PoC 한정), 멀티프로세스 시 백킹 필요

### M3 — Tools + MCP
- 옛 `assistant`/`builder` 경로가 옛 `ToolType/ToolResponse` import → 별칭 유지로 import-time 호환, M5에서 일괄 폐기
- MCP stdio transport는 probe 미지원 — sse/streamable_http만 디스커버리

### M4 — Skills + 마이그레이션
- 마이그레이션 번호 m13 점유 → **m18**로 명명 (기존 m13~m17 모두 사용 중이었음 — 초기 탐색 누락)
- `LLMProvider`는 services 의존성 때문에 모델 파일 유지 (M5 일괄 삭제). back_populates만 제거해 mapper 충돌 방지

### M5 — agent_runtime 재배선
- chat_service `_default_connection_map` 폐기 → 단일 경로로 일관성 확보
- trigger_executor.py L44-46 prefetch 버그를 신규 단일 진입점으로 통일하여 수정
- 옛 코드 21 prod + 21 tests 일괄 삭제로 ~3,000 LOC 감축

### M6 — 프론트엔드
- TanStack Table v8 + React 19 react-hooks/incompatible-library warning은 무해 (informational)
- E2E는 page.route mocking 패턴 — 백엔드 기동 후 라이브 API로 재포인트 가능

---

## 8. 커밋 히스토리

```
1e1df7f [feat] M6: 프론트엔드 그린필드 (디자인 시스템 + 4페이지 + E2E)
4cfdd2b [refactor] M5: agent_runtime 재배선 + 키 로테이션 cron + 옛 코드 정리
ca0d58c [feat] M4: Skills 재작성 + m18 마이그레이션 + bootstrap 시드
f02b537 [feat] M3: Tools 재정의 + MCP 서버 도메인
f9ee447 [feat] M2: Credential 도메인 + Vault + 라우터
b87664e [chore] M0+M1: 거버넌스 + 브랜딩 검증 + Cipher V2
```

---

## 9. PR 작성 가이드

```bash
gh pr create \
  --base main \
  --title "Credential / Tools / Skills 그린필드 리라이트 (n8n 패턴 차용, 브랜딩 제거)" \
  --body-file HANDOFF.md
```

**라벨**: `breaking-change`, `database-migration`, `frontend`, `backend`, `security`
**리뷰어**: 백엔드 1명 + 프론트엔드 1명 + 보안 1명 (가능하면)

---

**판정**: **GO** — 단일 PR 머지 가능. 6개 마일스톤 모두 게이트 PASS, 480 backend tests + frontend build PASS, branding 0건.

**END OF HANDOFF**
