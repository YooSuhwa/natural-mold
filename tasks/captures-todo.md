# 전체 앱 캡처 + 핵심 플로우 E2E

브랜치: `test/full-app-captures` (genui HEAD 기준 — 생성 UI 카드 포함).
산출물: PNG → `output/captures/<wave>/` (gitignore라 로컬). 스크립트만 커밋.
콘텐츠: 현실적 scripted 픽스처(플로우블한 에이전트/도구). E2E는 2개 플로우만(나머지는 캡처 투어).

## 실행 환경 (throwaway 스택)
- PG: host 5434 (genui에서 띄워둠), `alembic upgrade head` 적용됨.
- playwright webServer 자동 기동: `E2E_FRONTEND_PORT=3100 E2E_BACKEND_PORT=8101`.
- 필수 env: `E2E_SCRIPTED_MODEL_ENABLED=true`(키리스 결정적 모델), `E2E_SEED_USER_ENABLED=true`(operator 화면용 super_user 시드), `E2E_TEST_HELPERS_ENABLED=true`, `RATE_LIMIT_ENABLED=false`, `E2E_LIVE_CHAT_SURFACES=1`(게이트).
- 캡처 스펙은 `E2E_CAPTURE_TOUR=1` 게이트로 일반 CI에서 skip.

## 재사용 인프라
- `e2e/fixtures.ts`: loginApi(시드 super_user), apiPostJson/GetJson/DeleteOk, API_BASE.
- `e2e/langgraph-v3-helpers.ts`: setupLangGraphV3Agent, sendMessage, waitForActiveRun/RunStatus, approveExecuteInSkill.
- `e2e/chat-surfaces-live-captures.spec.ts`: capture(page,file) 패턴.
- scripted 마커: E2E_CHAT_RICH_OUTPUTS, E2E_HITL_APPROVAL/MULTI, E2E_TOOL_GROUP, E2E_SEARCH_GROUP, E2E_ASK_USER_FRUIT, E2E_LANGGRAPH_V3, E2E_DOCX/XLSX/PPTX/HWPX, E2E_TOKEN_USAGE_STREAM, E2E_SLOW_STREAM, E2E_UI_DATA_* (genui).

## 웨이브

### Wave 0 — 하네스 + 현실적 시드 팩토리
- [ ] `e2e/captures/_capture-helpers.ts`: CAPTURE_ROOT=output/captures, capture(page,wave,file), 뷰포트, 현실적 에이전트 팩토리(플로우블 이름/프롬프트/도구).
- [ ] 스택 1회 기동 + 단일 캡처로 파이프라인 검증.

### Wave 1 — 핵심 플로우 2개 (진짜 E2E + 단계 캡처)
- [ ] `captures-flow-agent-creation.spec.ts`: 자연어 빌더로 에이전트 생성 → 결과 → 생성된 에이전트 채팅 테스트. (영상 2 플로우)
- [ ] `captures-flow-daily-conversation.spec.ts`: 일상 비서 멀티턴 — 인사→표/마크다운→검색→ask_user→도구그룹→생성UI 카드, 각 턴 캡처. (영상 3 플로우)

### Wave 2 — 페이지/라우트 투어 (캡처)
- [ ] 대시보드(/), /agents/new(+conversational/manual/template), 리소스 목록(/skills /tools /mcp-servers /marketplace 탭들 /artifacts), /usage, /settings/*(profile/appearance/credentials/agent-api/audit/security/memory/artifacts/models/schedules), 공유뷰(/shared), 인증(/login /register).

### Wave 3 — 다이얼로그/모달 (캡처)
- [ ] share, credential create/detail, tool create/detail, skill create/detail, mcp import/detail, install wizard(steps), publish wizard(steps), schedule create, model test, delete confirm, sub-agents picker, api-key created.

### Wave 4 — 채팅 UI 상태 매트릭스 (캡처)
- [ ] empty/opener, streaming, tool group, search group, HITL single/multi(승인 전/후), ask_user, reasoning, phase timeline, subagent, deepagents 패널, artifacts inline+rail+preview(docx/xlsx/pptx/hwpx), attachments, 생성UI(data_table/chart/stats/terminal), compaction marker, branch picker, token popover, context gauge, reconnect/stop.

### Wave 5 — operator/super_user 화면 (캡처)
- [ ] system-llm, system-credentials, admin-audit, marketplace-admin(moderation), models(system).

## 진행 로그
- (착수) Wave 0/1 시작.
