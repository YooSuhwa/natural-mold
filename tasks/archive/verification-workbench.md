# Verification — Agent Edit Workbench

**Date**: 2026-04-28
**Verifier**: 베조스 (Jeff Bezos / QA DRI)
**Branch**: `feature/agent-edit-workbench`
**Plan**: `~/.claude/plans/image-41-ticklish-sky.md`
**판정 (재검증)**: **GREEN** — 자동 게이트 전 항목 PASS. YELLOW 사유였던 MAJOR 2건이 hotfix로 해소됨. 잔여 MINOR 1건(이미지 제거 placeholder)은 다음 PR로 이연 가능한 수준.

> **재검증 일시**: 2026-04-28 (hotfix after initial YELLOW)
> **재검증자**: 베조스
> **이전 판정**: YELLOW (MAJOR 2 / MINOR 1)
> **현재 판정**: GREEN (MAJOR 0 / MINOR 1)

---

## 자동 게이트 (모두 PASS)

| 게이트 | 명령 | 결과 |
|---|---|---|
| Backend migration | `cd backend && uv run alembic upgrade head` | OK (이미 head, 멱등 OK) |
| Backend tests | `cd backend && uv run pytest` | **628 passed**, 1 deselected, 17.82s — 젠슨 baseline 유지 |
| Backend lint | `cd backend && uv run ruff check .` | **All checks passed!** |
| Frontend build | `cd frontend && pnpm build` | **Compiled successfully in 4.0s**, TypeScript 4.4s, 14/14 static pages |
| Frontend lint | `cd frontend && pnpm lint` | **0 error / 0 warn** |

전 게이트 PASS — CHECKPOINT.md done-when 충족.

---

## 회귀 영역 점검 (저커버그 잔여 5건)

### 1. VisualSettingsFlow 내장 Save vs 워크벤치 단일 Save → ⚠️ **MAJOR (회귀 확인됨)**

`frontend/src/components/agent/visual-settings/visual-settings-flow.tsx`
- L48~63: 내부 state 11종을 자체 보유 (name/description/systemPrompt/modelId/temperature/topP/maxTokens/selectedToolIds/selectedSkillIds/selectedMiddlewareTypes)
- L126~153: 내부 `handleSave()` — `useUpdateAgent(agentId)` 직접 호출
- L368~374: `<Toolbar onSave={handleSave} ... />` 그대로 렌더

`frontend/src/app/agents/[agentId]/settings/page.tsx`
- L298~312: `tab === 'visual'`일 때 `<VisualSettingsFlow ... />` inline 렌더, 어떠한 prop으로도 내장 Toolbar/Save 를 끄지 않음
- L247~252: 헤더에도 별도의 `<Button onClick={handleSave}>` 존재

**결과**: 비주얼 모드 전환 시 화면에 Save 버튼이 2개 보인다.
- 데이터 손실 위험: 낮음 (각자 자기 state에서 동일한 update API를 호출하므로 race가 있어도 마지막 호출이 이김)
- isDirty 신호 분리 위험: **있음** — 사용자가 비주얼 모드에서 ToolboxNode를 토글하면 VisualSettingsFlow 내부 state만 변하고, 페이지 헤더의 isDirty 는 false 로 유지되어 "저장" 버튼이 비활성. 사용자는 Toolbar의 내장 Save 를 눌러야만 저장됨 → 폼/비주얼 양쪽 변경이 동시 존재하면 한쪽이 다른 쪽을 덮어씀.

**권고**: 다음 중 하나
- (a) `VisualSettingsFlow` 에 `embedded?: boolean` prop 신설 → true일 때 Toolbar 숨김 + 내부 state 대신 props로부터 controlled 모드로 전환
- (b) M8 HANDOFF에 "비주얼 모드는 단독 라우트(`/agents/[id]/visual-settings`)에서만 사용" 명시하고 워크벤치 [비주얼] 탭은 read-only 미리보기로 격하

### 2. SettingsPanel "이미지 제거" toast placeholder → MINOR

`_components/right-panel/settings-panel.tsx:28~31, 67~71`
- `handleRemove()` 가 `toast.info(tc('comingSoon.default'))` 만 표시
- 버튼은 항상 노출됨 (`{imageUrl && ... 이미지 제거 ...}`)
- 사용자 confusion 위험: 중간 — 클릭해도 이미지가 사라지지 않고 토스트만 떠서 "버그인가?" 의심 가능
- **권고**: 다음 PR 전까지 버튼을 disabled + 툴팁 "곧 지원" 으로 변경하거나, 메뉴 자체에서 숨김

### 3. TestChatPanel이 streamAssistant(Fix endpoint) 재사용 → MAJOR (UX 미스매치)

`_components/right-panel/test-chat-panel.tsx:34~38`
- `streamFn = streamAssistant(agentId, content, signal, sessionId)` — Fix(meta-agent) 엔드포인트 사용
- 컴포넌트 주석에 "MVP: streamAssistant(Fix endpoint)를 재사용. 별도 ephemeral conversation 엔드포인트가 생기면 streamFn만 교체하면 된다."로 명시

**문제**: [테스트] 탭이 사용자가 만든 에이전트의 실제 행동을 미리보기하는 것이 아니라, "에이전트를 어떻게 수정할까" 메타 응답을 받음. PRD/스펙의 "신규 — 일반 에이전트 자유 대화 채팅" 정의와 불일치.

**위험도**: 높음 — 사용자가 [테스트]를 신뢰하면 실제 에이전트 동작을 잘못 평가.

**권고**: M8 직전 / 다음 스프린트로 ephemeral conversation 엔드포인트 신설 태스크 분리. 단기적으로는 [테스트] 탭에 "Fix 패널 미리보기" 라벨/배너 추가.

### 4. `/agents/[id]/visual-settings` 별도 라우트 보존 → OK

`pnpm build` 결과 `ƒ /agents/[agentId]/visual-settings` 라우트 정상 생성. 페이지 컴포넌트(`visual-settings/page.tsx`)도 그대로 동작 — 회귀 없음. 다음 PR에서 `redirect()` 처리 예정 (스펙 명시).

### 5. 행별 [⚙] config edit placeholder → 무관 (실제 노출되지 않음)

`tools-middlewares-grid.tsx:163~181` 의 `Row` 컴포넌트는 `onConfig` prop이 있을 때만 [⚙] 버튼을 렌더. 현재 `ToolsBox` / `MiddlewaresBox` 어디에서도 `onConfig`를 전달하지 않음 (L60~67, L101~107). → 사용자에게 [⚙] 자체가 보이지 않음. 스펙의 "곧 지원 toast" 시나리오는 아직 실행 경로 없음. 회귀 없음.

`section-model.tsx`, `section-sub-agents.tsx` 의 [⚙]은 각각 ModelDialog / SubAgentsDialog를 여는 정상 동작.

---

## 폐기 잔존 검증

```bash
grep -rn "basic-info-tab\|model-tab\|tools-skills-tab" frontend/src/ | grep -v node_modules
```

**결과**: 0 hits. 폐기 3건의 import / 참조 전부 제거됨.

`ls _components/`:
```
dialogs/  form-mode/  right-panel/  triggers-tab.tsx
```
폐기된 3개 파일은 git 상태에서 `D` 로 표시됨 (status snapshot 확인).

---

## 시나리오 검증 (정적 코드 리뷰)

| 시나리오 | 파일:라인 | 결과 |
|---|---|---|
| `/agents` 대시보드 정상 | `app/page.tsx` (변경 없음) | OK — Agent 타입에 `opener_questions` optional 추가만 (lib/types/index.ts:18) |
| `/agents/new` 정상 | `app/agents/new/*` (변경 없음) | OK — creation flow 미수정 |
| `/agents/[id]/conversations/[cid]` empty state 오프너 | `conversations/[cid]/page.tsx:185~224` | OK — `agent.opener_questions ?? []` fallback, 길이 0이면 버튼 안 보임. `composer?.setText(q)` 안전 호출 (optional). |
| 헤더 인라인 편집 → 저장 | `settings/page.tsx:206~217, 247~252, 141~161` | OK — name/description Input → page state → handleSave payload 포함 |
| 폼 ↔ 비주얼 토글 | `settings/page.tsx:258~313` | OK 단, 위 #1 dual-save 회귀 |
| 행 [⚙] → 다이얼로그 | `section-model.tsx:44`, `section-sub-agents.tsx:43` | OK |
| +도구/+미들웨어 → 모달 | `tools-middlewares-grid.tsx:55, 96` | OK |
| [Fix] AssistantPanel 동작 | `right-panel.tsx:72~79`, `assistant-panel.tsx:21~88` | OK — `showHeader={false}` prop 정상 적용 |
| [테스트] 채팅 동작 | `test-chat-panel.tsx` | 동작 OK, 의미는 위 #3 회귀 |
| [오프너] 추가/삭제/저장 | `opener-editor.tsx` (구조 OK), `page.tsx:323~324` | OK — onChange → page state → save payload `opener_questions` (page.tsx:155) |
| [스케줄] 트리거 추가/삭제 | `right-panel.tsx:93~95`, `triggers-tab.tsx` 재사용 | OK — `onRequestDelete` callback 정상 wired (page.tsx:325, 343~348) |
| [설정] 이미지 생성/재생성/제거 | `settings-panel.tsx` | 생성/재생성 OK (`useGenerateAgentImage`), 제거는 placeholder (위 #2) |
| 미저장 [←] confirm | `page.tsx:172~183` | OK — `window.confirm(t('unsavedWarning'))`. beforeunload 도 132~139 라인에서 처리 |
| Backend opener_questions wiring | `models/agent.py:30`, `schemas/agent.py:15~36, 72~77, 93~98, 127`, `services/agent_service.py:67, 116~117`, `alembic/versions/m16_add_opener_questions.py` | OK — 마이그레이션 + 모델 + 스키마 (validator 12개 / 200자 / non-empty) + service create/update 경로 |

---

## 발견 이슈 요약

| # | 분류 | 영역 | 요지 |
|---|---|---|---|
| 1 | **MAJOR** | VisualSettingsFlow inline 사용 | 워크벤치 헤더 Save와 비주얼 내장 Toolbar Save 동시 노출 → isDirty 분리, 한쪽이 다른 쪽 덮어쓰기 가능 |
| 2 | MINOR | SettingsPanel "이미지 제거" | 클릭 시 toast만 뜨는 placeholder가 노출돼 사용자 confusion 가능 |
| 3 | MAJOR | TestChatPanel | Fix endpoint 재사용 → [테스트]가 메타 에이전트 응답을 보여줌. 실제 에이전트 동작 검증 불가 |
| 4 | — | visual-settings 단독 라우트 | 회귀 없음 (다음 PR redirect 예정) |
| 5 | — | 도구/미들웨어 행 [⚙] | 미노출 — 회귀 없음 |

**BLOCKER**: 0건
**MAJOR**: 2건 (#1, #3)
**MINOR**: 1건 (#2)

---

## 사용자 수동 검증 권장 항목 (5)

1. **[⚠️] 비주얼 모드 dual-save 체감**: 워크벤치에서 [비주얼] 탭 클릭 → 우측 상단 ToolBar Save 버튼이 헤더 Save 와 중복으로 보이는지 확인. 보이면 둘 중 하나로 저장 시도 → 폼 모드로 돌아가서 변경 반영 여부.
2. **[⚠️] [테스트] 탭 응답 성격**: "안녕" 입력 → 응답이 일반 어시스턴트 톤인지, "어떻게 수정할까요" 톤인지 확인. 후자면 issue #3 의 UX 미스매치 실증.
3. **오프너 end-to-end**: 워크벤치 [오프너]에서 질문 1~3개 추가 → 저장 → `/agents/[id]/conversations/new` 진입 → 빈 화면 버튼 클릭 → composer 텍스트 주입(전송 X) 확인.
4. **헤더 인라인 편집 + 새로고침**: 이름/설명 변경 → 저장 → F5 → 유지 확인.
5. **[설정] 이미지 제거 클릭**: 토스트만 뜨고 이미지 그대로 유지 → issue #2 실증.

---

## 판정 근거

- **자동 게이트**: 전 항목 PASS — done-when 충족
- **회귀 시나리오**: 데이터 손실 가능성 0, 폐기 잔존 0
- **치명적 결함**: 없음 (BLOCKER 0)
- **다만**: MAJOR 2건이 사용자에게 직접 노출되는 UX 회귀 — "Good enough"로 보지 말 것

→ **YELLOW**. 사티아 판단:
- (A) MAJOR 2건을 M7 안에서 즉시 수정 후 GREEN 재판정 → 권장
- (B) M8에 follow-up 태스크로 분리 + HANDOFF에 명시적 risk 등재 후 머지 → 차선

기술적으로 현 상태에서 main 머지 가능하나, 베조스 기준 "Day 1 mentality"로는 (A) 선택 권고.

---

## 재검증 (Hotfix Verification, 2026-04-28)

저커버그가 (A) 경로로 hotfix 진행. 베조스 재검증 결과 → **GREEN**.

### 자동 게이트 (재실행)
- `pnpm build`: PASS — 14/14 static pages, no TypeScript error
- `pnpm lint`: PASS — 0 error / 0 warn
- backend: 변경 없음 (재실행 생략)

### MAJOR #1 — VisualSettingsFlow dual-save → 해소 ✅

`components/agent/visual-settings/visual-settings-flow.tsx`
- L22~46: `ControlledVisualState` / `ControlledVisualHandlers` 인터페이스 신설 (state 11종 + handler 10종)
- L62~65: props에 `embedded?: boolean` + `controlledState?` + `controlledHandlers?` 추가
- L108: `isControlled = embedded && !!controlledState && !!controlledHandlers` 명시적 가드 — 셋 다 만족해야만 controlled (불완전 prop으로 사고 방지)
- L109~138: 모든 read/write 경로가 `isControlled ? controlled... : internal...` 분기로 통일
- L141~164: useEffect의 agent prop 동기화 / default model 셋업도 `if (isControlled) return` 가드 — controlled 모드에서 internal state setter가 우회됨 ✅
- L166~197: `toggleTool` / `toggleSkill` / `toggleMiddleware` callback도 `if (isControlled) controlledHandlers!.onToggleX(...) else setInternalSelectedX(...)` 분기 ✅
- L469~477: `{!embedded && <Toolbar ... />}` — embedded일 때 내장 Toolbar 자체를 렌더하지 않음 ✅

`app/agents/[agentId]/settings/page.tsx`
- L301~337: `<VisualSettingsFlow ... embedded controlledState={...} controlledHandlers={...} />` — 페이지 useState 값/setter를 그대로 위임. Set 토글은 `(prev) => toggleSetItem(prev, id)` 패턴으로 immutable 처리

**Backward compat 확인**:
- `app/agents/[agentId]/visual-settings/page.tsx`: `embedded` 미전달 → false → Toolbar 노출 + internal state 사용. 기존 동작 100% 유지 ✅
- `app/agents/new/manual/*`: grep 결과 0 hit. embedded 없음 ✅
- `pnpm build` Route 목록에 `/agents/[agentId]/visual-settings` 그대로 ƒ로 표시 ✅

**잔여 노트 (참고)**: embedded 모드에서도 내부 `useUpdateAgent`/`useCreateAgent` 훅은 인스턴스화되지만 호출 경로(handleSave)는 Toolbar 제거로 도달 불가. 메모리/네트워크 영향 없음. 향후 정리 시 useMutation도 조건부 분리 가능하지만 React Hooks 규칙(분기적 호출 금지) 때문에 현재 구조가 안전.

### MAJOR #2 — TestChatPanel banner → 해소 ✅

`_components/right-panel/test-chat-panel.tsx:53~57`
- 패널 최상단(thread 위)에 amber 톤 배너:
  ```
  ⚠ MVP: 현재 Fix 에이전트와 동일한 endpoint를 사용합니다. 일반 채팅 분리는 후속 PR에서 진행됩니다.
  ```
- 다크모드 대응: `border-amber-200 bg-amber-50 text-amber-900` (light) → `dark:border-amber-900/40 dark:bg-amber-950/40 dark:text-amber-200` (dark) ✅
- 컨테이너 `border-b`로 본문 thread와 시각 분리 ✅
- 사용자 confusion 위험: 해소 — 첫 인터랙션 전에 limitation 명시
- streamFn(Fix endpoint) 동작 자체는 변경 없음 (스펙대로 ephemeral endpoint는 후속 PR 분리)

**잔여 노트 (참고)**: 배너 문자열이 i18n 키가 아닌 하드코딩 한국어. 영문 사용자 노출 시 미번역. M8/HANDOFF에서 i18n 정리 권장하지만 이번 PR을 막을 수준은 아님 → 패스.

### MINOR #1 — 이미지 제거 placeholder

이번 hotfix 범위 밖. 상태 그대로 — 다음 PR로 이연. HANDOFF에 follow-up 항목으로 등재 권고.

### 신규 회귀 점검 (hotfix 자체에서 발생할 수 있는)
- VisualSettingsFlow 내부 `handleAgentNodeUpdate` 가 `setName`/`setDescription` 등 polymorphic setter를 사용 — controlled 모드에서 페이지 useState 호출 → 정상. uncontrolled 모드에서 internal setter 호출 → 정상.
- ReactFlow 노드 데이터(`nodes` state)가 `useNodesState(initialNodes)`로 한 번만 init되고 useEffect로 갱신됨(L296~). controlled 모드에서 page state가 변경되면 read 값(name/description/...)이 바뀌고 useEffect 의존성에 포함되어 노드도 재계산됨 → 정상 ✅
- `useEdgesState(computedEdges)` + useEffect 동기화도 동일 패턴 → 정상

### 최종 판정

| 항목 | 이전 | 현재 |
|---|---|---|
| 자동 게이트 | PASS | PASS |
| BLOCKER | 0 | 0 |
| MAJOR | 2 | **0** |
| MINOR | 1 | 1 (이연) |
| 폐기 잔존 | 0 | 0 |
| 판정 | YELLOW | **GREEN** |

→ **GREEN**. M7 done-when 충족. M8(HANDOFF) 진행 가능.
