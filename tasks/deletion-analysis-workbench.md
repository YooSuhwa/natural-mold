# Deletion Analysis — Agent Edit Workbench

**Status**: GREEN
**Owner**: 사티아 (M2 직접 수행)
**Date**: 2026-04-28

---

## 폐기 (Delete)

### 1. `frontend/src/app/agents/[agentId]/settings/_components/basic-info-tab.tsx`
- **이유**: 통합 워크벤치에서 분해됨
  - 이름/설명/이미지 → 헤더 인라인 (이미지는 우측 [설정] 탭)
  - systemPrompt → 좌측 `section-instructions.tsx`
- **호출처**: `settings/page.tsx`만 — 페이지 재작성 시 import 제거

### 2. `frontend/src/app/agents/[agentId]/settings/_components/model-tab.tsx`
- **이유**: 다이얼로그로 흡수
- **이전 대상**: `_components/dialogs/model-dialog.tsx` (ModelSelect + 슬라이더 + 리셋 버튼 콘텐츠 그대로)
- **호출처**: `settings/page.tsx`만

### 3. `frontend/src/app/agents/[agentId]/settings/_components/tools-skills-tab.tsx`
- **이유**: 모달들로 분해
  - tools 영역 → `_components/dialogs/add-tool-modal.tsx`
  - skills 영역 → `_components/dialogs/sub-agents-dialog.tsx`
  - middlewares 영역 → `_components/dialogs/add-middleware-modal.tsx`
- **호출처**: `settings/page.tsx`만

---

## 보존 + 재배치 (Preserve + Relocate)

### 4. `frontend/src/app/agents/[agentId]/settings/_components/triggers-tab.tsx`
- **이유**: 우측 [스케줄] 탭에서 그대로 import
- **변경**: 라벨만 i18n 키 `agent.settings.tabs.schedule = "스케줄"`로
- **재배치**: 그대로 두고 `right-panel.tsx`에서 import. 또는 `_components/right-panel/schedule-tab.tsx`로 re-export

### 5. `frontend/src/components/agent/assistant-panel.tsx`
- **이유**: 우측 [Fix 에이전트] 탭 콘텐츠 — 이미 "어떻게 수정할까요?" + suggestion 칩이 구현되어 있음
- **수정**: `showHeader?: boolean = true` prop 추가. RightPanel 내부에서는 `showHeader={false}`로 외곽 헤더 중복 방지
- **추가**: SUGGESTIONS 클릭 시 `useComposer().setText(suggestion)` 연결 (현재 `/* TODO */`)

### 6. `frontend/src/components/agent/visual-settings/visual-settings-flow.tsx`
- **이유**: 좌측 [비주얼] 탭에서 inline 렌더
- **수정 없음**: 이미 props 기반 컴포넌트 (`agent`, `agentId`, `models`, `tools`, `skills`, `middlewares`, `triggers`, `mode`)
- **호출처 추가**: `settings/page.tsx`에서 `tab === 'visual'`일 때 `<ReactFlowProvider><VisualSettingsFlow ... /></ReactFlowProvider>`

### 7. `frontend/src/app/agents/[agentId]/visual-settings/page.tsx`
- **이번 PR**: 유지 (deprecate 대상이지만 라우트 보존)
- **다음 PR**: `redirect('/agents/[agentId]/settings?tab=visual')` 또는 제거

---

## 신규 (New)

### Frontend

```
_components/
├── form-mode/
│   ├── form-mode.tsx
│   ├── section-instructions.tsx
│   ├── section-sub-agents.tsx
│   ├── section-model.tsx
│   └── tools-middlewares-grid.tsx
├── dialogs/
│   ├── model-dialog.tsx
│   ├── sub-agents-dialog.tsx
│   ├── add-tool-modal.tsx
│   └── add-middleware-modal.tsx
└── right-panel/
    ├── right-panel.tsx
    ├── test-chat-panel.tsx
    ├── opener-editor.tsx
    └── settings-panel.tsx
```

### Backend

```
alembic/versions/m16_add_opener_questions.py
```

수정:
- `app/models/agent.py` (+1 컬럼)
- `app/schemas/agent.py` (+1 필드, validator)
- `app/services/agent_service.py` (update 경로)
- `tests/test_agents.py` (+케이스)

---

## Scope Creep 플래그

다음은 **이번 PR에 포함하지 않음**:
- `[⚙]` 도구별 config 편집 (행 우측 톱니) — placeholder, "곧 지원" toast
- visual-settings 별도 라우트 deprecate (다음 PR)
- 모바일 반응형 정밀 튜닝 (lg: 이상에서 grid, 이하 stack 정도만)

---

## 영향도 회귀 체크리스트 (베조스 M7)

- [ ] `/agents` 대시보드 — 영향 없음 (Agent 타입에 opener_questions 추가만)
- [ ] `/agents/new` — 영향 없음
- [ ] 채팅 페이지 `/agents/[id]/conversations/[cid]` — 빈 화면 오프너 버튼 추가만 (회귀 X)
- [ ] AssistantPanel — `showHeader` prop 옵셔널, default true → 회귀 X
- [ ] TriggersTab — import 위치만 변경 → 회귀 X
- [ ] VisualSettingsFlow — 추가 호출 위치만 → 회귀 X

---

## 판정

**GREEN** — 폐기 3건은 단일 호출처(settings/page.tsx) 내 분해로 안전. 보존 4건은 prop 추가/import 위치 변경 수준이라 회귀 위험 낮음. 신규는 격리된 모듈.
