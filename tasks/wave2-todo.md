# Wave 2 구현 체크리스트 (feature/chat-wow-wave2)

정밀 검증(2026-07-04, main fdef4567) 기반. 각 항목은 커밋 단위로 진행.

- [x] W2-1 팀 스트립 — `subagent-team-strip.tsx` (커밋 6519530a)
  - useSubagentSnapshots + chatSubagentNames 표시명 치환, 칩 클릭 → 우측 레일
  - 검증: vitest 6/6, tsc
- [x] W2-2 검색 리치카드 (커밋 cce9293d)
  - parseSearchResults {items} + description/thumbnail/lprice, Tavily answer 박스
  - definition_key 이름 등록(naver_search_*, google_search_*) + shape 폴백 라우팅
  - 검증: vitest 14/14 + 관련 회귀 213, tsc, i18n
- [x] W2-3 Memory 회상 칩 (커밋 de71db42)
  - cfg.recalled_memories → moldy.memory_recalled stream-head 이벤트(stable id)
  - 프론트 replay:true 훅 + 상시 칩. 검증: pytest 293, vitest 8/8
- [x] W2-4/6 genui producer + 스킬 실행 카드 (커밋 56285b34)
  - UI_DATA_TOOL_TRANSFORMERS: execute_in_skill → terminal (OUTPUT_FILES 제거, 6k 캡)
  - SkillExecutionToolUI: 스킬명/커맨드/파일 칩(파일 API 링크)
  - 검증: pytest 277(agent_runtime)+projection, vitest 584(chat 스코프)
- [x] W2-5 E2E + 캡처
  - [x] scripted 픽스처: E2E_SEARCH_RICH(answer)/E2E_SEARCH_SHOP(items shape, thumbnail 키 필수 — image 상대경로는 http 가드에 걸림)
  - [x] captures-wave2-scenario.spec.ts (7 캡처, 1 passed 2.1m) — 기억 resetMemories(rerun-safe) + 말미 정리(user-scope 누출 방지)
  - [x] 백엔드 전체 pytest: 2520 passed (+5 skill-eval .env 의존 — SKILL_EVALUATION_ENABLED=true로 통과 확인)
  - [x] vitest 전체: 1219/1219
  - [x] 회귀 E2E: hitl-approval/chat-generative-ui 전부 green. **stale 3건 발견·갱신**:
    chat-stream-integrity :63/:299 + chat-langgraph-v3 :47 — PR #272가 승인 카드
    헤드라인을 스킬명(docx-document)으로 바꾸고 그룹 카드가 '승인 대기 N건'으로
    바뀐 뒤 갱신 안 된 단언들. **main 체크아웃 대조 실행으로 pre-existing 확증**
    후 새 계약으로 갱신 → 3건 모두 통과.
  - [x] 팀 스트립 ↳ 마커: SDK depth는 root=0/직접 위임=1 → 중첩 판정 depth>1로 수정
  - [x] 캡처 PNG 7장 사용자 전달

## 검증 커맨드
- backend: `uv run --with pytest-xdist pytest -q -n 8` (skill-eval 5건은 SKILL_EVALUATION_ENABLED=true 필요)
- frontend: `pnpm vitest run && pnpm exec tsc --noEmit && pnpm lint:i18n`
- 캡처: `E2E_CAPTURE_TOUR=1 E2E_FRONTEND_PORT=3310 E2E_BACKEND_PORT=8310 DATABASE_URL=...5436... DATABASE_URL_SYNC=... RATE_LIMIT_ENABLED=false E2E_TEST_HELPERS_ENABLED=true pnpm exec playwright test e2e/captures/captures-wave2-scenario.spec.ts`

## 알려진 함정 (이번 세션 발견)
- lint:design-system은 main에서도 exit 1 (pre-existing, message-attachments/approval-card)
- makeAssistantToolUI render 테스트: renderFn을 Provider "아래 컴포넌트" 렌더 중에 호출해야 context가 잡힘
- ui_data custom name은 무접두 "ui_data" (side-effect 관례; moldy.* 아님)
