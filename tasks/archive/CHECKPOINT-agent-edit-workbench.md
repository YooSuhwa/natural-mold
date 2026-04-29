# CHECKPOINT — Agent Edit Workbench (통합 워크벤치 리뉴얼)

**Plan**: `/Users/chester/.claude/plans/image-41-ticklish-sky.md`
**Branch**: `feature/agent-edit-workbench`
**Base**: `main @ 0609210`
**PO**: 사티아
**시작**: 2026-04-28

---

## 목표

`/agents/[agentId]/settings`를 기존 5개 탭 분리 구조에서 **좌(폼/비주얼 토글) / 우(Fix·테스트·오프너·스케줄·설정 5탭)** 통합 워크벤치로 리뉴얼한다. 헤더 이름·설명은 인라인 편집, 모델·서브에이전트는 다이얼로그, 도구·미들웨어는 2칸 그리드 + 모달. 백엔드는 `agents.opener_questions` JSON 컬럼 신설.

---

## M1 — 백엔드 `opener_questions` (젠슨 DRI)

- [ ] alembic `m16_add_opener_questions.py`
- [ ] `models/agent.py` Mapped 컬럼
- [ ] `schemas/agent.py` Response/Update/Create + validator (≤12, 1~200자)
- [ ] `services/agent_service.py` update 경로
- [ ] `tests/test_agents.py` PATCH 케이스
- 검증: `cd backend && uv run alembic upgrade head && uv run pytest && uv run ruff check .`
- done-when: 마이그레이션 적용, 전체 pytest PASS, ruff clean

## M2 — 디자인 스펙 + 삭제 분석 (사티아 직접)

- [ ] `docs/design-docs/agent-edit-workbench.md` (레이아웃·인터페이스·인라인 편집 패턴)
- [ ] `tasks/deletion-analysis-workbench.md` (basic-info-tab/model-tab/tools-skills-tab 폐기 분류)
- 검증: 두 파일 존재
- done-when: 저커버그가 spec만으로 구현 가능한 수준의 디테일

## M3 — 프론트엔드: 페이지 골격 + 헤더 인라인 (저커버그 DRI)

- [ ] `settings/page.tsx` 좌/우 grid 재작성, sticky save bar 제거
- [ ] 헤더: `[←]` + 작은 `AgentAvatar` + 이름·설명 ghost-input + `[🗑] [저장]`
- [ ] 좌측 [폼]/[비주얼] Tabs, 우측 [Fix][테스트][오프너][스케줄][설정] Tabs
- [ ] 페이지 state에 `openerQuestions: string[]` 추가, isDirty 비교 포함
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 PASS, 라우트 정상

## M4 — 프론트엔드: 좌측 폼 모드 + 다이얼로그 (저커버그 DRI)

- [ ] `_components/form-mode/{form-mode,section-instructions,section-sub-agents,section-model,tools-middlewares-grid}.tsx`
- [ ] `_components/dialogs/{model-dialog,sub-agents-dialog,add-tool-modal,add-middleware-modal}.tsx`
- [ ] 도구함/미들웨어 2칸 그리드, 행 레이아웃 (`name [⚙][🗑]`)
- [ ] 폐기: `basic-info-tab.tsx`, `model-tab.tsx`, `tools-skills-tab.tsx`
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드/린트 PASS, 모달 4종 동작

## M5 — 프론트엔드: 좌측 비주얼 inline + 우측 패널 (저커버그 DRI)

- [ ] `tab === 'visual'`일 때 `<VisualSettingsFlow>` inline 렌더
- [ ] `_components/right-panel/{right-panel,test-chat-panel,opener-editor,settings-panel}.tsx`
- [ ] [Fix]는 기존 `AssistantPanel` 재사용 (`showHeader` prop 추가)
- [ ] [스케줄]은 기존 `triggers-tab.tsx` 재사용
- [ ] [설정]은 이미지 생성/재생성/제거 전용
- 검증: `cd frontend && pnpm build`
- done-when: 5탭 전환 동작, 비주얼 모드 노드 그래프 표시

## M6 — 프론트엔드: 새 채팅 빈 화면 오프너 + 연결 (저커버그 DRI)

- [ ] 새 채팅 empty state에서 `agent.opener_questions` 버튼 렌더
- [ ] 클릭 시 composer에 텍스트 주입(전송 X) — `useComposer` 훅
- [ ] `lib/types/agent.ts` 타입 보강
- [ ] `lib/hooks/use-agents.ts` update payload에 `opener_questions`
- [ ] i18n 키 추가 (`messages/ko.json` 외)
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 새 대화 진입 시 오프너 버튼 표시 + 클릭 동작

## M7 — 통합 검증 (베조스 DRI)

- [ ] backend: `uv run pytest` 전체 + `uv run ruff check .`
- [ ] frontend: `pnpm build` + `pnpm lint`
- [ ] 회귀 시나리오: 기존 페이지(/agents 대시보드, 대화 페이지, /agents/new) 영향 없음
- [ ] 기능 시나리오: 헤더 인라인 편집 / 폼 ↔ 비주얼 / 우측 5탭 / 오프너 추가·저장·새대화 표시
- 검증: 종합 보고서 `tasks/verification-workbench.md`
- done-when: 판정 GREEN

## M8 — HANDOFF + 정리 (사티아)

- [ ] HANDOFF.md 갱신
- [ ] tasks/lessons.md 추가
- [ ] AUDIT.log PROJECT_DONE
- [ ] TeamDelete
