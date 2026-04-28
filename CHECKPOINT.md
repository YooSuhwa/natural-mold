# CHECKPOINT — Builder v3 (8-phase StateGraph + Chat UI 통합)

**Plan**: `/Users/chester/.claude/plans/kind-squishing-shore.md`
**Branch**: `feature/builder-v3-state-graph`
**Base**: `main @ e5876f2`
**PO**: 사티아
**시작**: 2026-04-26

---

## 목표

자연어 에이전트 생성 페이지(`/agents/new/conversational`)를 LangGraph StateGraph 기반 8-phase로 재설계하고, 일반 채팅 UI(`assistant-thread.tsx`)와 통합한다. HiTL(ask_user/approval/이미지 skip-generate)을 그래프 토폴로지로 강제한다.

## M0: 사일로 초기화

- [x] 새 브랜치 `feature/builder-v3-state-graph` 생성
- [x] 이전 progress.txt 아카이브 (`tasks/lessons-m6-1-archived-*.md`)
- [x] CHECKPOINT.md 작성
- [x] AUDIT.log 갱신
- 검증: `git status` 브랜치 확인
- 상태: done

## M1: 삭제 분석 (베조스 DRI)

- [ ] 폐기 예정 파일 목록 확정 (builder/orchestrator.py, builder/sub_agents/, _components/builder-thread.tsx, _components/phase-timeline.tsx, use-builder-runtime.ts, stream-builder.ts)
- [ ] 보존 vs 이식 vs 삭제 분류
- [ ] `tasks/deletion-analysis.md` 작성
- 검증: `cat tasks/deletion-analysis.md` 존재
- done-when: 모든 파일 분류 완료, sub_agents의 LLM 프롬프트/JSON 스키마 보존 영역 명시
- 상태: done

## M2: 아키텍처 설계 (피차이 DRI)

- [ ] `BuilderState` TypedDict 스키마 확정
- [ ] 8-phase StateGraph 토폴로지 다이어그램 + ADR 작성
- [ ] interrupt payload 계약 (ask_user / approval / choice 3종)
- [ ] SSE 이벤트 스키마 통일안 (기존 streaming.py 호환)
- [ ] `docs/design-docs/builder-v3-architecture.md` 작성
- 검증: `cat docs/design-docs/builder-v3-architecture.md` 존재
- done-when: ADR + state.py 시그니처 + 노드 인터페이스 정의됨
- 상태: done

## M3: 백엔드 구현 (젠슨 DRI)

- [ ] `builder_v3/state.py`, `todos.py`, `image_gen.py`
- [ ] `builder_v3/nodes/phase{1..8}_*.py`, `router.py`
- [ ] `builder_v3/graph.py` 컴파일
- [ ] `routers/builder.py` 신규 엔드포인트 (`/messages`, `/messages/resume`)
- [ ] `services/builder_service.py` graph.astream 통합
- [ ] pytest 단위 테스트 + 그래프 도달성 테스트
- 검증: `cd backend && uv run pytest && uv run ruff check .`
- done-when: 신규 테스트 통과, 기존 회귀 없음, ruff 0 warning
- 상태: done

## M4: 프론트엔드 구현 (저커버그 + 팀쿡 DRI)

- [ ] `lib/chat/use-chat-runtime.ts` conversationId → contextId 추상화
- [ ] `lib/sse/stream-builder-message.ts`, `stream-builder-resume.ts`
- [ ] Tool UI 5종 신규 (phase-timeline, recommendation-approval, prompt-approval, image-generation, draft-config)
- [ ] `app/agents/new/conversational/page.tsx` AssistantThread 기반 재작성
- [ ] 기존 `_components/*` 미사용 파일 제거
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: build 성공, lint 0 error, 기존 채팅 페이지 회귀 없음
- 상태: done

## M5: 통합 검증 (베조스 DRI)

- [ ] Backend pytest 전체 회귀
- [ ] Frontend build + 기존 채팅 페이지 회귀 확인
- [ ] 브라우저 E2E: mockup 이미지 1~4 흐름 재현
- [ ] 수정 요청 분기, 이미지 skip/generate 시나리오 검증
- done-when: 모든 시나리오 PASS
- 상태: done

## M6: 정리 + HANDOFF

- [ ] 폐기 파일 제거
- [ ] HANDOFF.md 작성
- [ ] tasks/lessons.md 업데이트
- [ ] PR 생성 (단계별 분리 권장)
- 상태: done

---

# CHECKPOINT (확장) — 채팅 화면 박스 카드 레이아웃 (Image #22)

**Plan**: `/Users/chester/.claude/plans/image-22-distributed-parasol.md`
**시작**: 2026-04-27
**PO**: 사티아
**팀**: 저커버그 단독 (UI 리디자인, plan이 매우 상세)

## 목표
`/agents/[agentId]/conversations/[conversationId]` 채팅 페이지를 박스 카드 레이아웃으로 재구성. 좌측 사이드바 + 메인 채팅 영역을 각각 흰색 카드(rounded-xl + shadow + border)로 분리, 페이지 배경은 muted 톤. 좌측 패널은 에이전트 카드 + 대화 목록 + 휴지통 풋터로 재구성.

## M_CHAT_BOX_1: 페이지 박스 레이아웃 + 헤더 단순화 (저커버그)
- [ ] page.tsx 루트 → bg-muted/30 + p-3 + gap-3, 두 영역 각각 rounded-xl/border/shadow-sm 카드
- [ ] showConversationList state 제거, 헤더를 [제목 + ⋯ 드롭다운(새 대화/설정)]로 단순화
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공
- 상태: done

## M_CHAT_BOX_2: ConversationList 재설계 (저커버그)
- [ ] 에이전트 카드 헤더 (아바타 md + 이름 + 설명 + 편집/설정 아이콘)
- [ ] "대화" 라벨 + "+" 버튼 row
- [ ] Pinned/Recent 라벨 제거 (pinned 우선 정렬은 유지)
- [ ] 활성 항목 emerald 톤 강조
- [ ] 휴지통 풋터 (ComingSoonButton)
- [ ] props 추가: agentImageUrl, agentDescription
- 검증: `cd frontend && pnpm build`
- 상태: done

## M_CHAT_BOX_3: 시간 포맷 유틸 + 메시지 시간 표시 (저커버그)
- [ ] `lib/utils/format-relative-time.ts` 신규 (오늘/어제/M.D)
- [ ] ConversationList 시간 라벨 교체
- [ ] AssistantThread User/Assistant 메시지에 시간 라벨 추가
- 검증: `cd frontend && pnpm build`
- 상태: done

## M_CHAT_BOX_4: i18n 키 추가 (저커버그)
- [ ] ko.json: `chat.conversationList.label`, `chat.conversationList.trash`, `common.yesterday`, `common.comingSoon.trash`
- [ ] 다른 로케일 동일 추가
- 검증: `cd frontend && pnpm build` (next-intl 누락 키 검출)
- 상태: done

## M_CHAT_BOX_5: 통합 검증 (사티아 + 베조스 옵션)
- [ ] `pnpm lint && pnpm build` 전체 PASS
- [ ] 이미지 #22와 시각 일치 (박스 분리감, 카드 형태)
- 상태: done
