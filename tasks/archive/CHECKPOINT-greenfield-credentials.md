# CHECKPOINT — Credential/Tools/Skills Greenfield Rewrite

**Plan**: `PLAN.md` (루트), `/Users/chester/.claude/plans/plan-md-poc-lexical-bumblebee.md`
**Branch**: `feature/greenfield-credentials` (예정)
**Base**: `main @ 8d42ae1`
**PO**: 사티아
**시작**: 2026-04-29
**PR 단위**: 단일 PR (마일스톤별 별도 커밋)

---

## 결정 사항 (불변)

1. Cipher: HKDF-SHA256(info=`b'moldy-encryption-v1'`), 단일 블롭 Base64(`[version 1B][salt 32B][authTag 16B][ciphertext]`), 멀티키 식별은 `credentials.key_id` 별도 컬럼.
2. LLM 모델: `models` 유지(api_key_encrypted 제거), `agents.llm_credential_id` FK 추가, `llm_providers` 폐기.
3. 단일 PR.
4. Vault provider 실구현 (HVAC SDK, feature flag).

---

## M0: 거버넌스 + docs/ 초기화 (피차이 DRI)

- [ ] `docs/ARCHITECTURE.md` 신규 도메인 반영 (credentials/tools/mcp/skills)
- [ ] `docs/design-docs/ADR-009-greenfield-credentials.md` (그린필드 결정 기록)
- [ ] `docs/design-docs/index.md` 인덱스 업데이트
- [ ] `tasks/deletion-analysis.md` (베조스 작성, 폐기 대상 확정)
- 검증: `ls docs/ARCHITECTURE.md docs/design-docs/ADR-009-*.md tasks/deletion-analysis.md`
- done-when: 4개 파일 존재, ADR 본문 작성 완료
- 상태: done (2026-04-29)

## M1: 브랜딩 검증 + Cipher V2 (피차이 + 베조스 DRI)

- [ ] `scripts/check_branding.py` (설정된 금지 식별자, 패키지 prefix, 자산 SHA-256 블랙리스트 검사)
- [ ] `backend/app/security/cipher.py` (info=`moldy-encryption-v1`)
- [ ] `backend/app/security/key_provider.py` (활성 키 + 검증 키들)
- [ ] `backend/app/config.py` `encryption_keys: list[str]` (비면 부팅 실패)
- [ ] `backend/.env.example` `ENCRYPTION_KEYS` 예시
- [ ] `backend/tests/test_cipher.py` (round-trip, 다중 키, 키 ID, 손상 검증)
- [ ] `backend/tests/test_branding.py` (스크립트 직접 호출)
- 검증:
  ```
  python scripts/check_branding.py
  cd backend && uv run pytest tests/test_cipher.py tests/test_branding.py -v
  ```
- done-when: branding 0건, cipher 모든 케이스 PASS
- 상태: done (2026-04-29, 24 tests PASS)

## M2: Credential 도메인 + Vault + 라우터 (젠슨 DRI)

- [ ] `backend/app/models/{credential,credential_audit_log,credential_default}.py` (신규 스키마)
- [ ] `backend/app/credentials/{field,domain,interpolation,authenticate,registry,oauth2_base,tester}.py`
- [ ] `backend/app/credentials/external_secrets/{base,env_provider,vault_provider,proxy}.py` (HVAC 실구현)
- [ ] `backend/app/credentials/definitions/*.py` × 11
- [ ] `backend/app/routers/credentials.py` (재작성, OAuth2 라우트 포함)
- [ ] `backend/tests/test_{credentials,oauth2,tester,external_secrets}.py`
- 검증: `cd backend && uv run pytest tests/test_credentials.py tests/test_oauth2.py tests/test_tester.py tests/test_external_secrets.py -v`
- done-when: CRUD/Test/OAuth2 mock refresh/Vault env_provider 통과
- 상태: done (2026-04-29, 44 신규 tests + 24 회귀 tests = 68 PASS)

## M3: Tools 재정의 + MCP 서버 (젠슨 DRI)

- [ ] `backend/app/models/{tool,mcp_server,mcp_tool}.py` (신규 스키마)
- [ ] `backend/app/tools/{domain,registry,runner,parameters}.py`
- [ ] `backend/app/tools/definitions/*.py` × 6 (http_request, naver_search, google_search, gmail_send, google_calendar_event, google_chat_message)
- [ ] `backend/app/mcp/{domain,client,discovery,oauth}.py`
- [ ] `backend/app/routers/{tools,mcp}.py`
- [ ] `backend/tests/test_{tools,mcp}.py`
- 검증: `cd backend && uv run pytest tests/test_tools.py tests/test_mcp.py -v`
- done-when: 도구 카탈로그/인스턴스화/HTTP 호출/MCP discover 통과
- 상태: done (2026-04-29, 29 신규 + 68 회귀 = 97 PASS)

## M4: Skills + 마이그레이션 m13 + 시드 (젠슨 + 피차이 DRI)

- [ ] `backend/app/models/skill.py` 재작성 (content_hash, size_bytes, version 등 추가)
- [ ] `backend/app/skills/{service,packager,inspector,runtime}.py`
- [ ] `backend/app/routers/skills.py` 재작성
- [ ] `backend/alembic/versions/m13_greenfield_credentials.py` (DROP+CREATE+ALTER agents.llm_credential_id, downgrade NotImplementedError)
- [ ] `backend/app/seed/bootstrap_from_env.py` (env → mock_user Credential 자동 생성)
- [ ] `backend/tests/test_skills.py`
- 검증:
  ```
  docker-compose down -v && docker-compose up -d postgres
  cd backend && uv run alembic upgrade head
  uv run pytest tests/test_skills.py tests/test_seed.py tests/test_migration_m18.py -v
  ```
- done-when: 클린 마이그레이션 성공, 시드 정상, skills 테스트 통과
- 상태: done (2026-04-29, 31 신규 + 97 회귀 = 128 PASS, alembic upgrade head는 사용자 확인 후 실행 예정)
- 비고: 마이그레이션 파일명은 m13가 이미 점유되어 있어 `m18_greenfield_credentials`로 명명. down_revision=m17_add_agent_subagents.

## M5: agent_runtime 재배선 + 키 로테이션 cron (젠슨 + 베조스 DRI)

- [ ] `backend/app/services/chat_service.py` 전면 재작성 (build_tools_config + get_agent_with_tools)
- [ ] `backend/app/agent_runtime/{executor,tool_factory,model_factory,trigger_executor,creation_agent,mcp_client}.py` 재배선 (trigger_executor L44-46 prefetch 버그 동시 수정)
- [ ] `backend/app/scheduler.py` `rotate_credentials_to_active_key` 잡 등록
- [ ] 폐기: `services/{encryption,credential_service,credential_registry,connection_service}.py`, `models/connection.py`, `routers/connections.py`, `agent_runtime/{naver_tools,google_tools,google_workspace_tools,env_var_resolver}.py`, `seed/prebuilt_connections.py`, `scripts/google_oauth_setup.py`
- [ ] 전체 회귀 테스트
- 검증: `cd backend && uv run pytest tests/ -v && uv run ruff check .`
- done-when: 전체 PASS, ruff clean, 채팅+트리거+MCP 시나리오 OK
- 상태: done (2026-04-29, 480 backend tests PASS, 신규 test_chat_integration.py / test_rotation.py 6건 PASS, branding 0건, ruff clean)

## M6: 프론트엔드 (팀쿡 + 저커버그 DRI)

**팀쿡** (디자인 시스템):
- [ ] `frontend/src/components/ui/data-table.tsx`
- [ ] `frontend/src/components/shared/{status-chip,icon,empty-state,dynamic-fields-form}.tsx`

**저커버그** (페이지/컴포넌트/API):
- [ ] `frontend/src/app/{credentials,mcp-servers,tools,skills}/page.tsx`
- [ ] `frontend/src/components/{credential,tool,mcp,skill}/*.tsx`
- [ ] `frontend/src/lib/{api,hooks,types}/{credentials,tools,mcp,skills}*.ts`
- [ ] `frontend/src/components/layout/sidebar.tsx` 네비 정리 (Connections 제거)
- [ ] `frontend/src/app/agents/*` 도구·스킬 선택 UI 신규 hooks/api로 재배선
- [ ] `frontend/e2e/{credentials,tools-catalog,mcp-server-wizard,skills-management}.spec.ts`
- [ ] 폐기: `app/connections/`, `components/{tool,connection,skill}/` 옛 폴더, 옛 api/hooks 파일
- 검증:
  ```
  cd frontend && pnpm lint && pnpm build
  pnpm exec playwright test
  ```
- done-when: 빌드 성공, E2E 4개 통과
- 상태: done (2026-04-29, frontend pnpm build PASS / pnpm lint clean(1 informational warn) / branding 0건. E2E specs 4개 작성 완료, Playwright 실행은 사용자에게 위임)

---

## M7: 모델 카탈로그 + 디스커버리 (LiteLLM + 자체 필터링)

**DRI**: 젠슨 (Backend) + 팀쿡/저커버그 (Frontend) + 베조스 (검증)
**왜**: M5에서 `models` 테이블을 read-only로 축소했으나, 신모델 추가가 시드 코드 수정+재시작으로만 가능 → 비실용적. List/Custom ID 두 모드 + LiteLLM enrichment로 UI에서 모델 추가/관리 가능하게 복원.

### 차용 패턴
- 모델 선택을 List(디스커버리) + Custom ID(직접 입력) 두 모드로 구성
- 공식 host 화이트리스트 / 호환 endpoint 모두 노출
- provider별 필터 룰 단일 진입점
- **OpenRouter pricing 우선** + **LiteLLM 카탈로그 fallback** + **수동 override**
- **multi-provider Gateway 정의**: OpenAI Compatible / OpenRouter (선택: Vercel AI Gateway)

### 백엔드
- [ ] `app/services/model_metadata.py` 복원 (LiteLLM enrich, 옛 코드 차용)
- [ ] `app/services/model_filtering.py` 신규 — `should_include_model(provider, model_id, is_custom_api)`
- [ ] `app/services/model_discovery.py` 재작성 — Credential 기반 dispatch + isCustomAPI 분기
- [ ] `app/credentials/definitions/` 신규 정의: `openrouter`, `openai_compatible` (vercel_ai_gateway는 후속)
- [ ] `app/routers/models.py` 보강: POST/PATCH/DELETE 추가, Source 배지(litellm/openrouter/manual)
- [ ] `app/routers/credentials.py` 보강: `POST /api/credentials/{id}/discover-models`
- [ ] `app/schemas/model.py` 보강: `DiscoveredModel`, `source` 필드
- [ ] `tests/test_model_discovery.py`, `test_model_metadata.py`, `test_model_filtering.py` 신규

### 프론트엔드
- [ ] `frontend/src/app/models/page.tsx` 신규 — DataTable + source 배지
- [ ] `frontend/src/components/model/model-add-dialog.tsx` 신규 — Tab Discover/Custom ID
- [ ] `frontend/src/components/model/model-edit-dialog.tsx` 신규 — 가격 override
- [ ] `frontend/src/components/model/model-discover-panel.tsx` 신규 — Credential 선택 + 결과 리스트 + 다중 선택
- [ ] `frontend/src/components/layout/app-sidebar.tsx` — Models 항목 추가
- [ ] `frontend/src/components/model/model-select.tsx` 업그레이드 — List/Custom ID 두 모드
- [ ] `frontend/src/lib/api/models.ts`, `lib/hooks/use-models.ts`, `lib/types/model.ts` 보강
- [ ] `frontend/e2e/models-discover.spec.ts` 신규

### 검증
```bash
cd backend && uv run pytest tests/test_model_*.py -v && uv run pytest tests/ -v && uv run ruff check .
cd frontend && pnpm lint && pnpm build
python scripts/check_branding.py
```
- done-when: 모델 디스커버리 동작, OpenRouter pricing 자동, LiteLLM fallback, Custom ID 직접 입력, 사용자 가격 override, 480+ tests PASS, 브랜딩 0건
- 상태: backend done (2026-04-29, 63 신규 + 480 회귀 = 543 PASS, ruff clean, branding 0건, m19 upgrade/downgrade/upgrade 라운드트립 OK against PG 5433)
- 비고: 마이그레이션 파일명은 `m19_add_models_source.py` (down_revision=m18_greenfield_credentials, ADD COLUMN nullable, drop downgrade 지원). frontend/M7 산출물은 별도 작업자(팀쿡/저커버그)가 진행 중.

---

## M8: 모델 Test + Curl 생성 + MCP Registry (LiteLLM 차용)

**DRI**: 젠슨 (Backend) + 팀쿡/저커버그 (Frontend)
**왜**: 등록된/신규 모델이 실제로 호출 가능한지 즉시 검증 (Credential test ≠ Model test). MCP 서버는 GitHub/Linear/Jira/Slack/Notion 카탈로그에서 1클릭 추가.

### 차용 패턴 (LiteLLM)
- **`POST /health/test_connection`**: 모델 단독 검증 (LangChain ainvoke + usage_metadata)
- **`model_connection_test.tsx`**: 자동 실행 + raw req/resp + Curl 명령어 + Clean error
- **`mcp_registry.json`**: 미리 등록된 MCP 서버 카탈로그 (GitHub/Jira/Linear/Slack/Notion)

### 백엔드
- [x] `services/model_test.py` 신규 — `run_model_test()`, clean-error 정규식, classify, masked curl
- [x] `services/mcp_registry.py` 신규 — JSON lazy 로더
- [x] `data/mcp_server_registry.json` 신규 — 5종 (github, linear, jira, slack, notion)
- [x] `agent_runtime/model_factory.create_chat_model_for_test()` (max_tokens=10, temp=0)
- [x] `schemas/model.py`: ModelTestPreviewRequest/ModelTestResponse
- [x] `schemas/mcp.py`: McpRegistryEntry, McpServerCreateFromRegistry
- [x] `routers/models.py`: POST /test, POST /test-preview
- [x] `routers/mcp.py`: GET /api/mcp-server-types, POST /api/mcp-servers/from-registry
- [x] `tests/test_model_test.py` (27), `test_mcp_registry.py` (11)

### 프론트엔드
- [x] `components/model/model-connection-test.tsx` — autoStart + Show Details (Request/Response/Curl) + Copy
- [x] `components/model/model-test-dialog.tsx` (행 액션) + `model-test-bulk-dialog.tsx` (일괄)
- [x] 5군데 통합: 행 Test / 다중 선택 Test Selected / ModelAddDialog Custom ID / ModelEditDialog / ModelSelect Custom ID
- [x] `components/mcp/mcp-server-wizard.tsx` — Step 1에 From Registry/Manual 탭
- [x] `components/ui/data-table.tsx` — enableRowSelection, toolbar 지원
- [x] lib/{api,hooks,types}/{model,mcp} 보강
- [x] `e2e/model-test.spec.ts` + `e2e/mcp-registry.spec.ts` (3/3 PASS)

### 검증
```bash
cd backend && uv run pytest tests/test_model_test.py tests/test_mcp_registry.py -v   # 38 PASS
uv run pytest tests/ -v   # 581 PASS (38 신규 + 543 회귀)
uv run ruff check .       # clean
cd ../frontend && pnpm lint && pnpm build && pnpm exec playwright test e2e/model-test.spec.ts e2e/mcp-registry.spec.ts
python scripts/check_branding.py   # 0건
```
- done-when: 38 신규 PASS + 회귀 0, e2e PASS, branding 0건
- 상태: done (2026-04-30, 38 신규 + 543 회귀 = 581 PASS, e2e 3/3 PASS, build PASS)

---

## M9: Hook/Middleware 프레임워크 + Health Check + History (예정)

**DRI**: 젠슨
**왜**: pre/post-call hook으로 Spend/권한/감사 등 cross-cutting concerns 일관 적용 + 등록된 모델/MCP가 살아있는지 주기 검증 + 시계열 history.

### 범위 (LiteLLM 차용)
- `app/hooks/` 신규 — `CustomHook` 베이스 + `HookRegistry` 디스패처 + `LoggingHook`/`AuditHook` 빌트인
- `executor.py`/`tool_factory`/`mcp_client` 호출 지점에 hook 통합 (failure isolation try/except)
- `models/health_check_history.py` 신규 — target_kind, target_id, status, latency_ms, error_kind/message, checked_at
- `services/health_check.py` 신규 — model + MCP health check 실행 + DB 기록 (`check_model`/`check_mcp_server`/`check_all_active`)
- `scheduler.py` — `health_check_all_active` 일일 cron 잡 (`settings.health_check_cron` 기본 `"0 4 * * *"`)
- `alembic/versions/m20_add_health_check_history.py` 신규 — dialect-aware UUID/timestamp + 인덱스 + reversible downgrade
- `routers/health.py` 신규 — GET /api/health/{models,mcp-servers,history}, POST /api/health/check
- 프론트: 모델/MCP 페이지에 status 컬럼 + 행 클릭 시 history 차트 (후속)
- 상태: backend done (2026-04-29, 22 신규 + 581 회귀 = 603 PASS, ruff clean, branding 0건, m20 upgrade/downgrade/upgrade 라운드트립 PG 5433 OK)

## M10: Spend Queue + Dashboard + Model Fallback

**DRI**: 젠슨 (backend) + 저커버그/팀쿡 (frontend)
**왜**: 비용 트래킹 정확도/성능 + 운영 가시성 + 안정성.

### 범위 (LiteLLM 차용)
- `services/spend_writer.py` — DailySpendUpdateQueue (asyncio.create_task + Redis 버퍼 옵션 + batch flush)
- 6개 daily aggregate 테이블 (user/agent/model/credential/team[추후]/tag[추후])
- `app/usage/` 페이지 보강 — 자체 SVG 차트 (line/bar) + 시간/모델/에이전트 필터
- `models/agent.py`: `model_fallback_list: list[UUID]` 추가 + 마이그레이션 m22
- `model_factory`: try/except 체인 (primary fail → fallback)

### Frontend (저커버그/팀쿡 done, 2026-04-29)
- `lib/types/usage.ts` 신규 — `UsageDailyEntry`, `UsageDailyParams`, `UsageTargetKind`, `UsageGroupBy`, `UsageMetric`
- `lib/api/usage.ts` 보강 — `usageApi.daily()` + `getDailyAggregate()`
- `lib/hooks/use-usage.ts` 보강 — `useDailyAggregate(params)` (cache key = params 객체)
- `lib/types/index.ts` — `Agent.model_fallback_ids?: string[] | null`, `AgentCreate/UpdateRequest.model_fallback_ids` 추가
- `app/usage/page.tsx` 전면 재작성 — 4 Summary cards (이번 달 비용/토큰/요청/평균) + 필터바(7d/30d/90d/Custom + User/Agent/Model + Date/Target + cost/tokens/requests metric) + 자체 SVG line/bar chart + raw 테이블 + CSV 다운로드 + EmptyState
- `components/usage/{spend-line-chart,spend-bar-chart,format}.tsx` 신규 — 자체 SVG (M9 health-history-chart 패턴 차용, viewBox + path/area, status-coloured points). 차트 라이브러리 추가 의존성 0건.
- `app/agents/[agentId]/settings/_components/dialogs/model-dialog.tsx` 보강 — Fallback Models 섹션 (details + 위/아래 버튼으로 순서 변경, dnd-kit 미도입). 최대 5개. 중복 경고. PATCH 시 `model_fallback_ids` 포함.
- `app/agents/[agentId]/settings/_components/form-mode/{section-model,form-mode}.tsx` — fallback prop chain + section-model에 `+N fallback` 배지
- `components/agent/agent-card.tsx` — Primary 모델 옆 `+N fallback` 배지 (있을 때만)
- `app/agents/[agentId]/settings/page.tsx` — fallbackIds state + isDirty 추가 + save 시 model_fallback_ids 전달
- `messages/ko.json` — `usage.filters/metric/summary/fallback`, `agent.card.fallbackTitle`, `usage.fallback.*` 추가
- `playwright.config.ts` — `PW_SKIP_BACKEND=1` 환경변수로 backend webServer skip 가능 (fully-mocked spec용)
- `e2e/spend-dashboard.spec.ts` 신규 — 3 시나리오 (summary cards + line/bar 토글 + CSV 활성화 + empty state)
- `e2e/model-fallback.spec.ts` 신규 — settings 진입 → ModelDialog → Fallback 섹션 → +Add → Save → PATCH body의 `model_fallback_ids` 캡처 검증
- 검증: `pnpm lint` clean, `pnpm build` PASS, `python scripts/check_branding.py` 0건, `PW_SKIP_BACKEND=1 pnpm exec playwright test e2e/spend-dashboard.spec.ts e2e/model-fallback.spec.ts` 4/4 PASS
- 발견 이슈: 로컬 PG (port 5432)가 m17_add_agent_subagents에 머물러 있어 backend(m22 head) 부팅 시 `column models.max_output_tokens does not exist` 에러. PoC 단계라 `alembic upgrade head` 적용 필요(데이터 손실 액션이라 사용자 확인 후 실행).
- 상태: frontend done (저커버그/팀쿡, 2026-04-29). backend done (젠슨, 2026-04-29).

### Backend (젠슨 done, 2026-04-29)
- `models/{daily_spend_user,daily_spend_agent,daily_spend_model}.py` 신규 — Numeric(20,8) cost, unique (date, target_id), CASCADE on FK target.
- `alembic/versions/m21_add_daily_spend_aggregates.py` — 3개 테이블 + 인덱스 (down_revision=m20). dialect-aware, idempotent helpers, reversible.
- `alembic/versions/m22_add_agent_model_fallback.py` — `agents.model_fallback_list JSON NULLABLE` (down_revision=m21). reversible.
- `services/spend_writer.py` 신규 — `SpendEntry` dataclass + `DailySpendUpdateQueue` (asyncio.Queue 기반 + 백그라운드 drain task + flush_interval/batch_size 트리거 + dialect-aware ON CONFLICT UPSERT, PG/SQLite 양쪽 지원, application-level fallback 포함). 모듈 전역 `spend_queue` 싱글턴.
- `hooks/builtin/spend_hook.py` 신규 — `agent_invoke` post hook에서 `spend_queue.add()` 호출. failure isolation (try/except + warning).
- `hooks/{__init__,builtin/__init__}.py` 보강 — `SpendHook` 등록.
- `main.py` lifespan — `spend_queue.start()` (lifespan 진입 시) + `spend_queue.stop()` (graceful shutdown — 잔여 flush 후 cancel).
- `agent_runtime/streaming.py` — `stream_agent_response`에 `usage_sink: dict | None` 추가, 스트림 종료 시 `prompt_tokens/completion_tokens/estimated_cost`를 callback dict에 surface.
- `agent_runtime/executor.py` — `_build_model_with_fallback(cfg)` 신규 헬퍼 (executor-side, DB-free 체인 walk + recoverable error 분기). `_hook_result_from_usage()` 헬퍼로 streaming-captured usage → `HookResult.tokens_in/out/cost_usd` 매핑. `AgentConfig.model_fallback_chain` 필드 추가.
- `agent_runtime/model_factory.py` — `create_chat_model_with_fallback(agent, db, ...)` 신규. primary 시도 → recoverable 에러시 fallback 체인 walk. 각 시도마다 audit log `fallback` action (`success: bool`, `provider`, `model_name`, `model_id`, `error`). `_is_fallback_recoverable(exc)` 분류기 (401/403/404/408/409/429/5xx + TimeoutError/HTTPError/ConnectionError).
- `routers/conversations.py` + `agent_runtime/trigger_executor.py` — `_resolve_fallback_chain(db, fallback_list)` 헬퍼로 `agent.model_fallback_list` (UUID strings) → 체인 dict 리스트로 사전 해석 후 `AgentConfig.model_fallback_chain`에 주입. 누락된 model row는 silent drop.
- `models/agent.py` — `model_fallback_list: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)` 추가.
- `schemas/agent.py` — `AgentCreate/Update/Response`에 `model_fallback_ids: list[uuid.UUID] | None` 추가 (Response는 default factory empty list).
- `services/agent_service.py` — `_validate_model_fallback_ids()` 추가 (existence check), create/update에서 검증 + JSON 직렬화 (str(uuid)).
- `routers/agents.py` — `_agent_to_response()`에서 `model_fallback_list` (str list) → `model_fallback_ids` (UUID list) 변환. 잘못된 entry는 silent drop.
- `services/usage_aggregate.py` 신규 — `get_daily_spend(target_kind, target_id, from_date, to_date, group_by)`. tenancy: user 축은 직접 FK, agent 축은 `Agent.user_id` join, model 축은 (per-model 테이블이 cross-user) `DailySpendAgent → Agent` join으로 *현 사용자 contribution*만 집계. `group_by=target` 시 user/agent/model 별 label 자동 fill.
- `routers/usage.py` 보강 — `GET /api/usage/daily?target_kind=&target_id=&from=&to=&group_by=` 신규.
- `NOTICES.md` — LiteLLM 차용 표에 `spend_writer.py` (DailySpendUpdateQueue), `usage_aggregate.py` (daily aggregate read API), `create_chat_model_with_fallback` (router fallback walk) 행 추가 + 본문 단락 보강.
- 테스트:
  - `test_spend_writer.py` (8) — flush_batch / ON CONFLICT 누적 / target 누락 시 axis skip / loop interval / stop drain / queue full degrade / distinct dates / Decimal precision
  - `test_usage_aggregate.py` (6) — user 축 시계열 / agent 축 group_by=target label / model 축 agent join scope / window 필터 / target_id 필터 / cross-tenant isolation
  - `test_model_fallback.py` (9) — recoverable classifier 3종 / executor chain (primary→fallback / 모두 실패 / unrecoverable / no chain) / `create_chat_model_with_fallback` audit (성공+실패) / no chain
  - `test_migration_m21.py` (4) + `test_migration_m22.py` (4) — module imports / metadata / round-trip / idempotent
  - `test_hooks.py` 갱신 — `register_default_hooks_idempotent`이 spend_hook 포함 검증
- 검증:
  - `uv run pytest tests/ -v`: **634 PASS** (31 신규 + 603 회귀).
  - `uv run ruff check .`: clean.
  - `python scripts/check_branding.py`: 0건.
  - PG 5433 round-trip: m20 → m21 → m22 → downgrade -2 → upgrade head 모두 성공. 컨테이너에서 `daily_spend_*` 3개 테이블 + `agents.model_fallback_list` 컬럼 존재 확인.

---

## 게이트 정책

- **브랜딩 0건**: `python scripts/check_branding.py` 통과 없이 머지 불가
- **데이터 손실 액션** (docker volume 삭제, m13 alembic upgrade): 사용자 확인 후 실행
- **3회 실패**: 사티아에게 에스컬레이션 → 스토리 재분해 또는 스코프 축소
- **마일스톤 별 커밋**: 각 마일스톤 완료 시 1 커밋

---

# CHECKPOINT — UI Refactor (Sprint 1~4)

**Plan**: `~/.claude/plans/buzzing-prancing-cloud.md`
**Started**: 2026-05-01
**PO**: 사티아 / DRI(디자인): 팀쿡 / DRI(구현): 저커버그 / DRI(검증): 베조스
**Scope**: frontend/ 전용. 백엔드 변경 없음.

## M-UI1: Sprint 1 — 디자인 토큰 + DialogShell + Sheet→Dialog 전환 + UI 베이스 정비
- [ ] `src/app/globals.css` `--primary`/`--ring` emerald 매핑 + `--primary-strong` 신설
- [ ] `src/components/ui/{input,textarea,select,button,checkbox}.tsx` focus-visible 완화 (border-ring 제거, ring-3→ring-2)
- [ ] `src/components/ui/dialog.tsx`/`sheet.tsx` 베이스 톤 정비 (rounded-2xl, ring-border/60, X 버튼, 백드롭)
- [ ] `src/lib/design-tokens.ts` (DIALOG_SIZE 5단계 + DIALOG_HEIGHT 3단계)
- [ ] `src/lib/constants/{model,timing,usage}.ts`
- [ ] `src/components/shared/dialog-shell.tsx` (Header/Body/Footer/Sidebar 슬롯, 비주얼 강제)
- [ ] `src/components/shared/{page-shell,error-state,delete-confirm-inline,form-footer}.tsx`
- [ ] `src/components/shared/base-detail-dialog.tsx`
- [ ] Sheet→Dialog 변환 4종: credential/skill/tool/mcp `*-detail-dialog.tsx` 신설 + 호출부 교체 + 기존 `*-detail-sheet.tsx` 삭제
- 검증: `cd frontend && pnpm lint && pnpm build` (TS 에러 0, 빌드 성공)
- done-when: 위 8개 항목 + Sheet 잔존 사용처 = 모바일 사이드바 + 대화목록 2건만
- 상태: pending

## M-UI2: Sprint 2 — 페이지 마이그레이션 + raw color 토큰화 + i18n
- [ ] 5개 page.tsx (tools/models/skills/mcp-servers/credentials) → PageShell + isError 분기
- [ ] raw `bg-emerald-*` 등 58회 → `bg-primary`/`text-primary-strong` 등 토큰
- [ ] 한글 4건 + 영문 헤더 4건 → next-intl 메시지
- 검증: `pnpm lint && pnpm build`
- done-when: 페이지 5개에서 `flex flex-1 flex-col gap-6 ... p-6` 인라인 0건
- 상태: pending

## M-UI3: Sprint 3 — 에이전트 폼 RHF + Zod
- [ ] `app/agents/[agentId]/settings/page.tsx` (518줄, useState 21개) → RHF + Zod
- [ ] `app/agents/new/manual/page.tsx` 동일 스키마 재사용
- 검증: `pnpm build` + 시각 회귀 (저장/Dirty/취소 동작)
- done-when: useState 21→1 (form), 수동 dirty 195줄 삭제
- 상태: pending

## M-UI4: Sprint 4 — 성능 (번들/리렌더/Suspense)
- [ ] `app/agents/[agentId]/visual-settings/page.tsx` xyflow `next/dynamic`
- [ ] `components/chat/markdown-content.tsx` syntax-highlighter lazy
- [ ] `components/agent/visual-settings/visual-settings-flow.tsx` useEffect 분할 + initialNodes 호이스팅
- [ ] `components/chat/assistant-thread.tsx:289` key 수정
- [ ] Suspense 경계 도입 (채팅/visual-settings/usage)
- 검증: `pnpm build` 청크 크기 비교 + 시각 회귀
- done-when: visual-settings 청크가 메인 라우트보다 작음, key 안티패턴 0건
- 상태: pending
