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
- 상태: pending

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
- 상태: pending

---

## 게이트 정책

- **브랜딩 0건**: `python scripts/check_branding.py` 통과 없이 머지 불가
- **데이터 손실 액션** (docker volume 삭제, m13 alembic upgrade): 사용자 확인 후 실행
- **3회 실패**: 사티아에게 에스컬레이션 → 스토리 재분해 또는 스코프 축소
- **마일스톤 별 커밋**: 각 마일스톤 완료 시 1 커밋
