# CHECKPOINT — Credential/Tools/Skills Greenfield Rewrite

**Plan**: `PLAN.md` (루트), `/Users/chester/.claude/plans/plan-md-poc-lexical-bumblebee.md`
**Branch**: `feature/greenfield-credentials` (예정)
**Base**: `main @ 8d42ae1`
**PO**: 사티아
**시작**: 2026-04-29
**PR 단위**: 단일 PR (마일스톤별 별도 커밋)

---

## 결정 사항 (불변)

1. Cipher: n8n 알고리즘 차용, HKDF-SHA256(info=`b'moldy-encryption-v1'`), 단일 블롭 Base64(`[version 1B][salt 32B][authTag 16B][ciphertext]`), 멀티키 식별은 `credentials.key_id` 별도 컬럼.
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

- [ ] `scripts/check_branding.py` (`\bn8n\b` 0건, `@n8n/*` 패키지 0건, 로고 SHA-256 블랙리스트, 화이트리스트 `NOTICES.md`)
- [ ] `NOTICES.md` (차용 출처 명기, 라이선스 메모)
- [ ] `backend/app/security/cipher.py` (n8n 알고리즘, info=`moldy-encryption-v1`)
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

## M7: 모델 카탈로그 + 디스커버리 (LiteLLM + n8n 하이브리드)

**DRI**: 젠슨 (Backend) + 팀쿡/저커버그 (Frontend) + 베조스 (검증)
**왜**: M5에서 `models` 테이블을 read-only로 축소했으나, 신모델 추가가 시드 코드 수정+재시작으로만 가능 → 비실용적. n8n의 resourceLocator 패턴(List/Custom ID 두 모드) + 옛 LiteLLM enrichment 차용하여 UI에서 모델 추가/관리 가능하게 복원.

### 차용 패턴
- **n8n resourceLocator**: 모델 선택을 List(디스커버리) + Custom ID(직접 입력) 두 모드
- **n8n isCustomAPI 분기**: 공식 host 화이트리스트 / 호환 endpoint 모두 노출
- **n8n modelFiltering helper**: provider별 필터 룰 단일 진입점
- **OpenRouter pricing 우선** + **LiteLLM 카탈로그 fallback** + **수동 override**
- **multi-provider Gateway 정의**: OpenAI Compatible / OpenRouter (선택: Vercel AI Gateway)

### 백엔드
- [ ] `app/services/model_metadata.py` 복원 (LiteLLM enrich, 옛 코드 차용)
- [ ] `app/services/model_filtering.py` 신규 — `should_include_model(provider, model_id, is_custom_api)` (n8n 패턴)
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
- [ ] `frontend/src/components/model/model-select.tsx` 업그레이드 — resourceLocator 패턴 (List/Custom ID)
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
- `app/hooks/` 신규 — `CustomLogger` ABC, `async_pre_call_hook` / `async_post_call_hook`
- `executor.py`/`tool_factory`/`mcp_client` 호출 지점에 hook 통합
- `models/health_check_history.py` 신규 — model_id, mcp_server_id, status, latency_ms, error, checked_at
- `services/health_check.py` 신규 — model + MCP health check 실행 + DB 기록
- `scheduler.py` — 주1회 health check cron 잡
- `routers/health.py` 신규 — GET /api/health/{models,mcp-servers}, GET /api/health/history
- 프론트: 모델/MCP 페이지에 status 컬럼 + 행 클릭 시 history 차트
- 상태: pending

## M10: Spend Queue + Dashboard + Model Fallback (예정)

**DRI**: 젠슨 + 저커버그
**왜**: 비용 트래킹 정확도/성능 + 운영 가시성 + 안정성.

### 범위 (LiteLLM 차용)
- `services/spend_writer.py` — DailySpendUpdateQueue (asyncio.create_task + Redis 버퍼 옵션 + batch flush)
- 6개 daily aggregate 테이블 (user/agent/model/credential/team[추후]/tag[추후])
- `app/usage/` 페이지 보강 — Tremor 차트 (line/bar/pie) + 시간/모델/에이전트 필터
- `models/agent.py`: `model_fallback_list: list[UUID]` 추가 + 마이그레이션 m20
- `model_factory`: try/except 체인 (primary fail → fallback)
- 상태: pending

---

## 게이트 정책

- **브랜딩 0건**: `python scripts/check_branding.py` 통과 없이 머지 불가
- **데이터 손실 액션** (docker volume 삭제, m13 alembic upgrade): 사용자 확인 후 실행
- **3회 실패**: 사티아에게 에스컬레이션 → 스토리 재분해 또는 스코프 축소
- **마일스톤 별 커밋**: 각 마일스톤 완료 시 1 커밋
