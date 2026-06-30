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

## 진행 로그 (결과)
- **Wave 0 하네스 ✓** — _capture-helpers.ts (capture/seed/scriptedModelId). 영속 스택(reuseExistingServer)으로 재실행 가속.
- **Wave 2 페이지 26/26 ✓** — 대시보드(시드 에이전트 5), agent-new(hub/manual/template), 리소스 목록(skills/tools/mcp/marketplace/artifacts), usage, settings/*(profile/appearance/credentials/agent-api/audit/security/memory/artifacts/models/schedules), operator(system-llm/system-credentials/admin-audit/marketplace-admin), auth(login/register). super_user 인증 검증됨.
- **Wave 4 채팅 상태 13/14 ✓** — rich markdown, tool group, search group, ask_user, 생성UI(table/chart/stats/terminal), HITL 승인(단일+멀티), artifact, langgraph-v3 planning, branch picker. (누락: empty-state — chat 라우트 콜드컴파일 반복 실패, 최소 중요도.)
- **Wave 3 다이얼로그 5/8 ✓** — credential/skill/mcp/model create + agent delete. (누락: tool/schedule/api-key create — 빈 상태 아이콘 CTA라 라벨 매칭 불가.)
- **Wave 1 hero 플로우** — 빌더 생성 플로우 ✓(05 welcome + 06 단계 타임라인+이름 제안, video 2 재현). 일상대화 멀티턴 ✗(240s 타임아웃; 컴포넌트는 Wave 4에 전부 존재).

총 **46장** → output/captures/{wave1-flows,wave2-pages,wave3-dialogs,wave4-chat-states}/

## Wave 6 — 코드/백엔드 개선 (사용자 피드백)
- [x] **차트 색상**: chart-card.tsx 막대/라인을 구분 팔레트로(민트 단색 → indigo/emerald/amber/...). ✓
- [ ] **ask_user 변형 4종**(scripted 픽스처 추가): ① 텍스트 입력(옵션 없음) ② 단일선택+기타(직접입력) ③ 다중선택(4개, maxSelections>1) ④ question_flow 멀티스텝. (기존: 단일선택 3개=fruit)
- [ ] (보류/확인) 승인 카드 args가 너무 기술적(raw dict 노출) → approval-card 렌더 개선 여부 사용자 확인.

## Wave 7 — 콘텐츠 있는/신규 캡처 (사용자 피드백)
**리치 시드 선행**: 도구·스킬·MCP·트리거 붙은 에이전트 + 대화 여러 개 + 아티팩트(docx+이미지) + 첨부 + usage 데이터.
- [ ] 에이전트 수정 — 내용 있는 상태 + 탭 전부(basic/tools/skills/mcp/subagents/triggers/memory/api/fallback) + **visual 수정**.
- [ ] 대시보드 — 에이전트 펼침(세션 리스트 보이는 형태) + **정렬/그룹 변형**(세션단위·에이전트별·정렬옵션).
- [ ] 사용량(usage) — 데이터 있는 상태.
- [ ] 스케줄 — 트리거 등록된 상태 + 발생 시 에이전트 **느낌표(attention) 배지**.
- [ ] 파일/아티팩트 리스트(이미지 포함) + **이미지 클릭 확대(lightbox)**.
- [ ] 채팅 — 에이전트 이름 옆 hover/click **요약 팝업**.
- [ ] 채팅 — **trace 화면** + trace에서 실제 대화 표현.
- [ ] 채팅 — **첨부**: composer 표시 + 메시지 버블 표현.
- [ ] 채팅 — **todo/플랜** (langgraph_v3 write_todos).
- [ ] 채팅 — **웹검색 도구 펼친** 상태(출처 전개).
- [ ] 채팅 — ask_user 변형 5종 캡처(Wave 6 픽스처 기반).
- [ ] empty-state 채팅 / rich-markdown 상단(메시지 element 캡처).

## 남은 보정(선택)
- 일상대화 hero 플로우: 첫 goto 콜드컴파일 + 멀티턴 누적이 240s 초과 → 타임아웃 상향 또는 사전 워밍 + 턴 축소.
- 3개 다이얼로그(tool/schedule/api-key): 빈 상태 아이콘 CTA → testid/getByLabel로 정확 타겟.
- empty-state 채팅 / rich-markdown 상단(내부 스크롤) → 메시지 element 캡처로 보정.
- 그럴싸함 강화: scripted 기본응답이 "E2E scripted document model is ready."라 일상대화 자연스러움엔 scripted 시나리오 보강 필요(별도 백엔드 작업).
