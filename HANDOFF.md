# HANDOFF — 서브에이전트 분리 완성 + Codex 리뷰 라운드 4회 통과 (세션 8, 2026-04-29 오후)

**Base**: `main @ 0609210` (세션 6 PR 머지 후) → `feature/agent-edit-workbench` 브랜치에 세션 7 + 8 누적 미커밋
**상태**: 모든 검증 GREEN — BE pytest **647 passed** / ruff clean / alembic m17 roundtrip OK / FE lint 0/0 / build 14/14
**규모**: 33 changed +2521/-1023 + 23 untracked. main 대비 56 파일.

---

## 이번 세션 핵심 변경

### 1. 서브에이전트 다이얼로그 UX 완성 (Image #68/69 시안)
- 행 압축: `[👥] 서브에이전트  이름1, 이름2 +N  [⚙]` 한 줄 button (text-[10px] summary, cursor-pointer, text-foreground/80 icon)
- 다이얼로그 좌/우 2단: "현재 서브에이전트(N)" + "사용 가능한 에이전트(검색)" 카드 리스트
- 다이얼로그 height 고정: `max-h-[60vh] sm:h-[60vh]` — 모바일 viewport overflow 방지

### 2. Codex 리뷰 라운드 4회 fix
- **R1 P1**: `agent_service.create/update`의 sub_agent_ids에 cross-tenant + 미존재 검증 (`_validate_sub_agent_ids_owned`) — 채팅 write_tool과 동일 보호
- **R1 P2**: 미존재 UUID → 4xx (이전엔 IntegrityError → 500)
- **R2 P1**: `settings/page.tsx` useEffect를 **dirty-aware sync**로 — `lastSyncedAgentRef`(server snapshot) 기준 필드별 비교, 사용자 미터치 필드만 server 값 sync, 손댄 필드는 보존
- **R2 P2**: `read_tools.get_agent_config` 응답에 `sub_agents` 필드 추가 — LLM이 ADD/REMOVE 워크플로우 1단계에서 현재 sub-agent 목록 인지
- **R3 (반박)**: P1-B "thread.append가 stream 시작 안 함"은 부정확 — assistant-ui v0.12.24 `ExternalThread.js:347-374`에서 `onNew?.()` 명시 호출 확인, 정상 작동

### 3. /simplify 후속 정리 (8건)
**Backend**
- Tool/Skill ID owned 검증 추가 (`_validate_tool_ids_owned`/`_validate_skill_ids_owned`) — sub_agent와 동일 보호 확장
- `_selectin_agent`/`helpers.py`에서 `selectinload(AgentSubAgentLink.sub_agent)` 단계 제거 (lazy="joined" 중복)
- `add_subagent_to_agent` **N+1 → 단일 IN 쿼리** (3-pass: parse → batch validate → append)
- skipped 메시지 한/영 혼용 정리 (`(already added)` → `(이미 추가됨)`, `(not found)` → `(찾을 수 없음)`)

**Frontend**
- `useChatRuntime.onStreamEnd((didMutate: boolean) => void)` — `MUTATION_PREFIXES`(add_/remove_/update_/edit_/delete_/enable_/disable_/create_) 매칭으로 정밀 invalidate. 단순 텍스트/조회 응답에 폼 refetch 안 함
- `clarifying-question-ui.tsx` cn nested → 3-state union (`'idle' | 'selected' | 'dimmed'`) + STATE_CLASS lookup
- `tools-middlewares-grid.tsx` Row의 `removeLabel` props sprawl 제거 → 내부 `useTranslations('common')`

### 4. BE 신규 테스트 6건 (총 642 → 647)
- `test_sub_agent_self_reference_reject`, `test_sub_agent_duplicate_ids_reject`, `test_sub_agent_cascade_delete`, `test_sub_agent_mix_self_and_valid_reject`, `test_sub_agent_cross_user_owner_rejected`
- `test_sub_agent_nonexistent_id_rejected`, `test_tool_ids_nonexistent_rejected`, `test_skill_ids_nonexistent_rejected`

---

## 다음 작업

| 우선 | 항목 | 영향 |
|---|---|---|
| 1 | **커밋/PR 생성** — 세션 6/7/8 변경 분할 또는 단일 PR | 상 |
| 2 | 브라우저 수동 검증 — Image #67 시안 일치, 채팅↔폼 양방향 동기화, dirty-aware sync edge case | 상 |
| 3 | LangGraph executor에 sub_agent_links → deepagents `task` 도구 변환·주입 (실제 실행 로직) | 중 |
| 4 | `agent_subagent.py`의 `lazy="joined"` 제거 가능성 — async 환경 회귀 없는지 별도 PR로 검증 | 하 |
| 5 | `useAgentFormState` 훅 추출 (settings/manual state 미러링 통합) — 시기상조 판정, 필드 추가 시 재평가 | 하 |
| 6 | `VisualSettingsFlow.Controlled / Standalone` 분리 (isControlled 11쌍 분기 제거) | 하 |
| 7 | `(user_id, name)` UNIQUE 제약 — Skill 동명 dedupe 근본 해결 (현재 application-side dedupe) | 하 |

---

## 주의사항

- **dirty-aware sync 회귀 위험**: 11개 필드별 `form === prev` 비교가 손으로 작성. 새 필드 추가 시 비교 누락하면 silent revert 회귀 — 단위 테스트 추가 권장
- **agent_subagent.py `lazy="joined"`**: 회귀 위험으로 유지 결정. SQL 비대 → 후속 PR에서 별도 검증 후 정리
- **assistant-ui thread.append 검증**: v0.12.24 `ExternalThread.js:347-374` `onNew?.()` 명시 — 다른 버전 업그레이드 시 동작 변경 가능성 주의
- **PRAGMA foreign_keys 미활성**: SQLite 테스트 환경. reverse cascade 등 SQLite로 검증 어려움 → PostgreSQL prod에서만 실 검증

---

## 핵심 파일 (이번 세션 수정)

**Backend**
- `app/services/agent_service.py` — `_validate_{sub_agent,tool,skill}_ids_owned` 3개 헬퍼, create/update에서 모두 호출
- `app/agent_runtime/assistant/tools/write_tools.py` `add_subagent_to_agent` (N+1 제거 + 한국어 통일)
- `app/agent_runtime/assistant/tools/read_tools.py` `get_agent_config` (sub_agents 필드 추가)
- `app/agent_runtime/assistant/tools/helpers.py` (sub_agent_links 명시 + selectinload sub_agent 제거)
- `app/models/agent_subagent.py` (`lazy="joined"` 사유 주석)
- `tests/test_agents.py` (sub_agent + tool/skill 검증 테스트 8건)
- `tests/test_assistant_write_tools.py` (한국어 매칭)

**Frontend**
- `src/lib/chat/use-chat-runtime.ts` (`onStreamEnd(didMutate)`, `MUTATION_PREFIXES`)
- `src/components/agent/assistant-panel.tsx` (didMutate 분기)
- `src/app/agents/[agentId]/settings/page.tsx` (dirty-aware sync `lastSyncedAgentRef`)
- `src/app/agents/[agentId]/settings/_components/dialogs/sub-agents-dialog.tsx` (좌/우 2단 + 고정 height + a11y)
- `src/app/agents/[agentId]/settings/_components/form-mode/section-sub-agents.tsx` (한 줄 컴팩트 행)
- `src/app/agents/[agentId]/settings/_components/form-mode/tools-middlewares-grid.tsx` (Row aria-label 정리)
- `src/components/chat/tool-ui/clarifying-question-ui.tsx` (3-state union)
- `messages/ko.json` (`subAgents.*` 확장, `common.remove`)

---

새 세션에서 "HANDOFF.md 읽고 PR 생성/브라우저 검증/executor 통합 진행" 등으로 이어가면 됩니다.

---

# HANDOFF — Workbench UX 다듬기 + Fix 캐릭터 + 서브에이전트 분리 (세션 7, 2026-04-29)

**Base**: 세션 6 워크벤치 PR 머지 후 `main @ 0609210`
**Branch**: `main` (clean) — 이번 세션 변경분 미커밋 시 별도 브랜치로 분리 필요
**상태**: Plan mode 활성 (`~/.claude/plans/image-41-ticklish-sky.md` — "ask_clarifying 직접입력 fix" 플랜 잔존, 이미 반영됨 → 새 세션에서 "서브에이전트 데이터 모델 분리" 플랜으로 갱신 권장)

---

## 이번 세션 누적 변경 (이미 적용됨)

### 1. 폼/탭 UX 다듬기
- `section-instructions`: `[field-sizing:fixed] flex-1 min-h-[200px]` — 자동 높이 풀림 방지
- TabsList wrapper에 `overflow-y-hidden` — 세로 스크롤바 제거
- BaseUI tabs underline variant `after:bg-emerald-500 data-active:text-emerald-600` (사이드바 칩과 분리)
- ghost-input 패턴(border-0 bg-transparent + hover/focus bg-muted/30) — 이름/설명 인라인 편집
- 오프너 UX: read/hover([✎][🗑])/edit + 추가는 별도 Dialog (n/12, 200자, 빈 reject)

### 2. 매뉴얼 생성 페이지 워크벤치 통합
- `/agents/new/manual/page.tsx` — settings 워크벤치와 동일 레이아웃 미러링 (좌 폼/비주얼 토글 + 우 5탭)
- `handleCreateModeFirstMessage`: createAgent → `sessionStorage('fix-initial-message')` → `router.replace(/agents/{id}/settings)`
- settings 페이지가 마운트 직후 sessionStorage carry-over 읽어 자동 전송 (`initialSentRef` 중복 방지)
- 대시보드 비주얼 버튼 제거 — 만들기는 매뉴얼 단일 진입점

### 3. Fix 캐릭터 시스템
- `public/agent-fix-hero.webp` (편집 모드) + `agent-create-hero.webp` (만들기 모드, 500x500 webp)
- `AssistantPanel`: `createMode` prop으로 hero/avatar 이미지 분기, `showHeader` prop으로 헤더 토글
- `AgentAvatar.publicAsset` prop 추가 — API_BASE prepend 우회 (정적 자산 시 필수)
- `FixHero.imageSrc` 있으면 점선 보더 없이 `size-44 sm:size-52` 이미지

### 4. ask_clarifying_question 도구 UI
- `chat/tool-ui/clarifying-question-ui.tsx` + `tool-ui-registry.ts` 등록
- 옵션 1~3 클릭: `useAui` + `aui.thread().append({content: [{type:'text', text:opt}]})` (SuggestionTrigger 패턴, composer 우회)
- "직접 입력": composer 호출 없이 picked/disabled 토글만 (optional composer가 setText 내부에서 throw하는 문제 회피)

### 5. 백엔드 assistant tools 확장
- `write_tools.update_agent_metadata(name?, description?)` 신규
- `update_chat_openers` / `get_chat_openers`: `agent.opener_questions` 컬럼 사용 (model_params 우회 폐기)
- `prompt.md`에 "Update agent name and description" capability 추가
- `add_subagent_to_agent` write_tool은 **PoC stub만 존재** — 실제 저장 모델 미결정

---

## 진행 중 / 다음 작업

### CURRENT — 서브에이전트 ≠ 스킬 (Image #66 wrong → #67 correct)
> "지금 서브 에이전트에 나오는건 스킬이야. 서브 에이전트는 현재 열린 에이전트 외에 이미 생성된 다른 에이전트들을 서브 에이전트로 불러오는걸 말하는거야"

- `section-sub-agents.tsx` 현재 `useSkills` 사용 → `useAgents` (현재 agentId 제외) 로 교체
- `dialogs/sub-agents-dialog.tsx` 스킬 체크리스트 → 에이전트 카드 선택(아바타+이름+설명) 리팩토링
- page state: `selectedSubAgentIds: Set<string>` 신설 (`selectedSkillIds`와 분리)
- **BE 결정 필요**: agent.sub_agent_ids 컬럼 vs 별도 join table → m17 마이그레이션
- 매뉴얼 페이지에도 동일 상태 미러링 필요

### 후속 (세션 6에서 carry, 미해결)
1. PR 생성 (m16 + 워크벤치 + 세션 7 다듬기 → 단일 또는 분할)
2. TestChatPanel ephemeral conversation endpoint 분리 (`/api/agents/{id}/test-chat`)
3. SettingsPanel 이미지 제거 BE API
4. 도구별 `[⚙]` config 편집 UI

---

## 주의사항

- **Plan mode 활성** — 새 세션 시작 시 plan 갱신 또는 ExitPlanMode 필요
- **AgentAvatar.publicAsset**: `/`로 시작하는 정적 자산은 반드시 명시. 누락 시 API_BASE prepend로 404
- **VisualSettingsFlow controlled props**: 워크벤치 embedded 모드일 때 internal Set updater 우회 — `isControlled` 분기 누락 주의
- **Composer optional**: `useComposerRuntime({optional:true})`도 setText/send 내부에서 throw 가능 — try-catch 또는 `useAui().thread().append` 패턴 사용
- **ask_clarifying "직접 입력"**: 사용자가 입력창에 직접 타이핑하는 의도 — composer 호출 금지

## 핵심 파일 (이번/다음 작업)

- `frontend/src/app/agents/[agentId]/settings/_components/form-mode/section-sub-agents.tsx` ⚠ 리팩토링 대상
- `frontend/src/app/agents/[agentId]/settings/_components/dialogs/sub-agents-dialog.tsx` ⚠ 리팩토링 대상
- `frontend/src/app/agents/[agentId]/settings/page.tsx` — `selectedSubAgentIds` state 추가 위치
- `frontend/src/app/agents/new/manual/page.tsx` — 매뉴얼 페이지 동일 상태 미러링
- `frontend/src/components/agent/assistant-panel.tsx` — createMode/initialMessage carry
- `frontend/src/components/agent/fix-hero.tsx` — imageSrc 분기
- `frontend/src/components/chat/tool-ui/clarifying-question-ui.tsx` — useAui append 패턴 참고
- `backend/app/agent_runtime/assistant/tools/write_tools.py` — add_subagent_to_agent stub
- `backend/app/models/agent.py` — sub-agent 저장 컬럼 추가 시

## 마지막 상태

- 브랜치: `main` (clean) — 이번 세션 변경분이 워킹 트리에 있다면 `git status`로 확인 후 feature 브랜치로 분리
- 검증: 세션 7 변경분 일부는 lint/build 미실행 — 새 세션 시작 시 `pnpm lint && pnpm build` 권장
- DB: `m16` 적용 상태, m17(sub-agent 저장)은 미작업

새 세션에서 "HANDOFF.md 읽고 서브에이전트 데이터 모델 분리부터 진행해줘" 또는 "다음 작업 #1 PR 생성" 등으로 이어가면 됩니다.

---

# HANDOFF — Agent Edit Workbench 통합 리뉴얼 (세션 6, 2026-04-28)

**Base**: `main @ 0609210` (PR #77 머지 후)
**Branch**: `feature/agent-edit-workbench`
**누적 변경**: 백엔드 5 파일 + 마이그레이션 m16 + 프론트엔드 18 파일(폐기 3 + 신규 13 + 수정 5) + docs/tasks 4
**검증 상태 (Final GREEN)**: backend pytest 628 PASS / ruff clean / alembic m16 라운드트립 OK / frontend pnpm build PASS / pnpm lint 0 error · 0 warn

---

## 이번 세션 핵심 변경

### 1. `/agents/[id]/settings` 통합 워크벤치 레이아웃
- 기존 5탭(basic·model·tools·triggers·assistant) 단일 컬럼 → **좌(폼/비주얼 토글) / 우(Fix·테스트·오프너·스케줄·설정 5탭)** 분할
- 헤더 인라인 편집: `[←]` + `<AgentAvatar size="sm">` + ghost-input 이름·설명 + `[🗑] [저장]`. **sticky save bar 폐기**
- 모바일: `lg:` 미만은 stack(좌→우 수직 배치)

### 2. 좌측 폼 모드 — 한 화면 통합
- `_components/form-mode/`: `form-mode.tsx`, `section-instructions.tsx`(collapsible+글자수+fullscreen Dialog), `section-sub-agents.tsx`, `section-model.tsx`, `tools-middlewares-grid.tsx`(2칸 그리드)
- 행 패턴: `[아이콘] name [요약] [⚙][🗑]`. `[⚙]`은 다이얼로그 오픈, `[🗑]`은 page state 즉시 제거

### 3. 다이얼로그 4종 (`_components/dialogs/`)
- `model-dialog.tsx` — 기존 ModelTab 콘텐츠 그대로 다이얼로그화 (ModelSelect + temperature/topP/maxTokens 슬라이더)
- `sub-agents-dialog.tsx` — useSkills + Checkbox 리스트
- `add-tool-modal.tsx` — useTools + Checkbox 리스트
- `add-middleware-modal.tsx` — useMiddlewares + Checkbox 리스트

### 4. 좌측 비주얼 모드 inline + dual-save 차단
- `tab === 'visual'`일 때 `<ReactFlowProvider><VisualSettingsFlow embedded controlledState controlledHandlers /></ReactFlowProvider>` inline 렌더
- `VisualSettingsFlow`에 **hybrid controlled/uncontrolled 패턴**: `embedded && controlledState && controlledHandlers`이면 controlled, 아니면 internal Set state(기존 동작 100% 유지)
- `embedded`일 때 내부 Toolbar 미렌더 → 헤더 단일 Save 버튼만 노출

### 5. 우측 패널 5탭 (`_components/right-panel/`)
- `right-panel.tsx` — 5탭 라우터
- **Fix 에이전트** = 기존 `AssistantPanel` 재사용 (`showHeader?: boolean` prop 신설, 기본 `true`). SUGGESTIONS 칩의 `/* TODO */` placeholder를 `useComposerRuntime().setText`로 채움
- **테스트** = 신규 `test-chat-panel.tsx` — MVP로 `streamAssistant` 재사용 + 상단 amber 톤 안내 배너("⚠ MVP: Fix 에이전트와 동일 endpoint, 일반 채팅 분리는 후속 PR")
- **오프너** = 신규 `opener-editor.tsx` — 행 추가/삭제, `n/12` 카운터, 200자 제한
- **스케줄** = 기존 `triggers-tab.tsx` 그대로 import (라벨만 "스케줄"로)
- **설정** = 신규 `settings-panel.tsx` — 이미지 생성/재생성/제거(useGenerateAgentImage). **이미지 제거는 backend API 부재로 toast.info(coming soon) placeholder**

### 6. 새 채팅 빈 화면 오프너 버튼 (Image #41 두 번째 시안)
- `conversations/[conversationId]/page.tsx`의 inline emptyContent를 `ChatEmptyState` 컴포넌트로 추출(provider 자식이어야 useComposerRuntime 사용 가능)
- `agent.opener_questions` 있을 때 rounded-full pill 버튼 그룹 렌더 → 클릭 시 composer 텍스트 주입(전송 X)

### 7. 백엔드 — `agents.opener_questions` 컬럼
- Alembic `m16_add_opener_questions.py` (m15 → m16, 라운드트립 OK)
- `Agent.opener_questions: Mapped[list[str] | None]` (`JSON, nullable=True, default=list`) — `middleware_configs` 패턴 그대로
- `AgentCreate`/`AgentUpdate`/`AgentResponse` 3곳 모두에 필드 + 공유 validator(`_validate_opener_questions`): ≤12개, strip 후 1~200자, 빈 항목 reject
- service `create_agent`/`update_agent` + router `_agent_to_response` 반영
- 테스트 4개 추가: roundtrip / 13개 reject / 빈 문자열 reject / 201자 reject

### 8. 폐기 (3건, 단일 호출처 분해)
- `_components/basic-info-tab.tsx` (헤더 + section-instructions로 분해)
- `_components/model-tab.tsx` (model-dialog로 흡수)
- `_components/tools-skills-tab.tsx` (3개 모달로 분해)

---

## 다음에 해야 할 작업

| 우선순위 | 항목 | 영향/작업 |
|---|---|---|
| 1 | **커밋/PR** (제안: BE m16 / FE 워크벤치 / 비주얼 hybrid+오프너 버튼 3분할 또는 단일 PR) | 중/하 |
| 2 | 사용자 수동 브라우저 검증 5건 (verification-workbench.md 참조) | 상/하 |
| 3 | TestChatPanel 진짜 ephemeral conversation endpoint 분리 — BE `/api/agents/{id}/test-chat` 신규 + FE streamFn 교체 | 상/중 |
| 4 | SettingsPanel 이미지 제거 BE API — `DELETE /api/agents/{id}/image` + S3/local cleanup | 중/하 |
| 5 | TestChatPanel 배너 문자열 i18n 분리 (현재 한국어 하드코딩) | 하/하 |
| 6 | `[⚙]` 도구별 config 편집 UI (현재 placeholder) | 중/중 |
| 7 | `/agents/[id]/visual-settings` 별도 라우트 deprecate (redirect to settings?tab=visual) | 하/하 |

## 주의사항 / 기지 위험

- **Next.js 16**: `frontend/AGENTS.md` + `node_modules/next/dist/docs/`. `params: Promise<...>` + `use(params)` 패턴 유지
- **VisualSettingsFlow controlled props**: 워크벤치에서 controlled 모드일 때 internal Set updater 우회 — `isControlled` 분기 필수. 수정 시 모든 useEffect/toggle callback에 분기 누락 주의
- **AgentUpdate.opener_questions**: `[]`로 클리어, `undefined`로 무변경. 빈 항목은 BE validator가 422 반환 — FE 추가 가드는 다음 PR로
- **PUT 메서드**: `PUT /api/agents/{id}` (PATCH 아님). FE의 `agentsApi.update`은 이미 PUT 사용
- **이미지 제거**: 현재 toast.info placeholder만. 사용자 confusion 가능

## 핵심 파일

- `frontend/src/app/agents/[agentId]/settings/page.tsx` — 워크벤치 컨테이너
- `frontend/src/app/agents/[agentId]/settings/_components/form-mode/*.tsx` (5)
- `frontend/src/app/agents/[agentId]/settings/_components/dialogs/*.tsx` (4)
- `frontend/src/app/agents/[agentId]/settings/_components/right-panel/*.tsx` (4)
- `frontend/src/components/agent/visual-settings/visual-settings-flow.tsx` — embedded+controlled props
- `frontend/src/components/agent/assistant-panel.tsx` — showHeader prop
- `frontend/src/app/agents/[agentId]/conversations/[conversationId]/page.tsx` — ChatEmptyState 추출 + 오프너 버튼
- `backend/alembic/versions/m16_add_opener_questions.py`
- `backend/app/{models,schemas,services}/agent.py` — opener_questions 필드/validator/service
- `backend/app/routers/agents.py` — `_agent_to_response`에 필드 추가

## 마지막 상태

- 브랜치: `feature/agent-edit-workbench` (uncommitted, 25 파일)
- backend dev / frontend dev: 미기동 (필요 시 사용자 기동)
- DB: `m16` 마이그레이션 적용됨
- 산출물 위치: `docs/design-docs/agent-edit-workbench.md`, `tasks/{deletion-analysis,verification}-workbench.md`

새 세션에서: "HANDOFF.md 읽고 PR 생성해줘" 또는 "다음 작업 #3 (test endpoint 분리) 진행" 등으로 이어가면 됩니다.

---

# 이전 세션 (세션 5, 2026-04-28) — 채팅 UI 안정화 + 시간 시스템 정착

**Base**: `main @ 4f8df0c` (PR #76 머지 후)
**누적 변경**: ~30 파일 (backend 7 + frontend 16 + alembic m15 + docs/지원 5)
**검증 상태**: backend ruff + 624 pytest / frontend lint + format + 257 tests + build 모두 PASS
**이전 세션 기록**: 본 파일 위쪽 섹션(세션 1~3) + git log 참조

## 이번 세션 핵심 변경

### 1. 채팅 박스 카드 레이아웃 (Image #22)
- `page.tsx`: 루트 `bg-muted/30 + p-3 + gap-3`, 좌/우 각 `rounded-xl border bg-card shadow-sm` 카드
- 헤더 단순화: 제목 + ⋯ 드롭다운(새 대화/설정)
- `ConversationList`: 에이전트 카드 헤더 + "대화" 라벨 + 휴지통 풋터(`toast.info` placeholder)

### 2. 시간 시스템 (가장 까다로웠음)
- **백엔드**: `MessageResponse`/`ConversationResponse`에 `UtcDatetime` annotation(`PlainSerializer`로 'Z' suffix). `m15_add_message_timestamps` 마이그레이션 — `Conversation.message_timestamps: dict[msg_uuid, iso]` 영구 저장으로 옛 메시지 시각이 송신 시 흔들리지 않게.
- **프론트엔드**: `lib/utils/format-relative-time.ts` 신규 — `Intl.DateTimeFormat(timeZone='Asia/Seoul')` 직접 사용 (use-intl wrapper의 timeZone 옵션이 일관되지 않아 우회). `parseTimestamp`로 'Z' 없는 string은 UTC로 가정.

### 3. 채팅 streaming 버그 (오늘 진단/fix)
- **list-content fix** (`backend/app/agent_runtime/streaming.py`): Anthropic multi-block content가 `list[dict]`로 와도 처리. 이전엔 `isinstance(delta, str)`만 처리해 tool 사용 시 token streaming이 0개였음. 지금은 `content_to_text` 공유 헬퍼로 평탄화.
- **메시지 refetch 깜박임 fix** (`use-chat-runtime.ts`): `setStreamingMessages([])`를 `finally`에서 즉시 호출 → refetch 도착까지 답변 사라지는 깜박임. `prevMessagesRef` rendering-time 비교로 messages 변경 후 clear.
- **scroll fix** (`assistant-thread.tsx`): `ThreadPrimitive.Root`/`Viewport`에 `min-h-0` — 메시지 많을 때 입력창 화면 밖으로 밀려나는 문제.
- **streaming tool_call dedupe** (`streaming.py`): `_INTERNAL_TOOL_NAMES` filter(`ToolSelectionResponse` 등 미들웨어 schema 노출 차단) + `(name, id)` 기준 dedupe.

### 4. UI 디테일
- 사용자 메시지 wrapper `flex flex-col items-end max-w-[80%]` (짧은 메시지 우측 여백 fix)
- 메시지 hover 시만 시간/복사 표시 (`MessageMetaRow` 추출)
- AI 아바타 emerald 배경 + `imageUrl` 변경 시 hasError 자동 reset (`prevImageUrl` rendering-time 패턴)
- Composer: 모델 좌측, 토큰 바 `ml-auto` 우측 정렬, send 버튼 `variant="emerald"`
- StreamingLoadingIndicator를 absolute(`-top-5 left-11`)로 띄워 답변 텍스트 위치 stable
- `Button` cva에 `emerald`/`emeraldStrong` variant 추가
- 이미지 webp 변환 (3.6MB → 142KB, -96%)
