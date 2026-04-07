# HANDOFF — Moldy UI/UX 전체 개선

## 변경 사항 요약

### Critical 수정 (4건)
- 도구/스킬/트리거 삭제 시 AlertDialog 확인 추가
- 모바일에서 채팅 대화목록 접근 가능 (Sheet)
- 도구 상세 Sheet→Dialog 변경 + 이벤트 전파 수정
- 설정 페이지 4탭 분리 + sticky 저장 바

### Major 개선 (9건)
- 에이전트 카드: 도구 이름 나열→개수 배지, 설명 강조, 호버 시 버튼
- 브레드크럼 네비게이션 추가 (app-header 통합)
- 사이드바: 설정/로그아웃 연결, 테마 토글 분리
- 앱 설정 페이지 신규 생성 (/settings)
- 설정 페이지: shadcn Slider/Checkbox 교체, 미저장 경고
- Coming Soon 패턴 (파일첨부, Chat, Undo/Redo)
- 대화형 생성: 취소/리셋/Phase1 Timeline/로딩 텍스트
- 사용량 페이지: 기간 선택기 + Agent 링크
- 스킬 페이지: 검색 + 타입 필터

### Minor 수정 (5건)
- agents/new 뒤로가기 버튼
- 채팅 헤더 모델명 표시
- 모델 필터 바 flex-wrap
- Separator(세로선) 제거
- i18n 키 다수 추가

## 아키텍처 결정
- 도구 상세를 Sheet→Dialog로 변경: 다른 페이지(모델, 스킬)와 UI 패턴 통일 + 이벤트 전파 충돌 해결
- Coming Soon 패턴: disabled 대신 toast.info — 미구현 기능을 grep 가능하게 유지
- 브레드크럼: 경로 기반 자동 생성, UUID→에이전트명은 TanStack Query 캐시 조회
- 설정 페이지: 탭 분리 (기본정보/모델/도구스킬/트리거) + isDirty 추적

## 삭제된 항목 (Musk Step 2)
- `@tanstack/react-query-devtools` (미사용 의존성)
- `agentUsage` export (미사용)
- `useSkill` export (미사용)
- `useCreateModel` export (미사용)
- `toggleSetItem` import (미사용 — 수정 완료)

## Ralph Loop 통계
- 총 스토리: 10개 (S0~S9)
- 1회 통과: 8개
- 팀쿡 리뷰 후 보완: 2개 (S7, S8 — 모바일 반응형 3건)
- 에스컬레이션: 0개

## 팀 구성
| 팀원 | 역할 | 담당 스토리 |
|------|------|-------------|
| 사티아 | PO/팀 리드 | 오케스트레이션 |
| 베조스 | QA | S0, S1, S9 |
| 팀쿡 | Design/UX | S2, 검수 |
| 저커버그 | Frontend | S3~S8 |

## 수정된 파일 목록 (16개)
1. `src/app/tools/page.tsx`
2. `src/app/skills/page.tsx`
3. `src/app/agents/[agentId]/settings/page.tsx`
4. `src/app/agents/[agentId]/conversations/[conversationId]/page.tsx`
5. `src/app/agents/new/conversational/page.tsx`
6. `src/app/agents/new/page.tsx`
7. `src/app/usage/page.tsx`
8. `src/app/settings/page.tsx` (신규)
9. `src/app/models/page.tsx`
10. `src/components/shared/breadcrumb-nav.tsx` (신규)
11. `src/components/layout/app-header.tsx`
12. `src/components/layout/app-sidebar.tsx`
13. `src/components/chat/chat-input.tsx`
14. `src/components/agent/agent-card.tsx`
15. `src/components/agent/visual-settings/toolbar.tsx`
16. i18n 메시지 파일

## 남은 작업
- [ ] 파일 첨부 기능 구현 (현재 Coming Soon)
- [ ] Chat/Undo/Redo 기능 구현 (비주얼 설정, 현재 Coming Soon)
- [ ] 사용량 차트/그래프 추가 (숫자만 있음)
- [ ] 설정 페이지 다이얼로그 로직 분리 (P3 — 코드 품질)

## 검증 결과
- `pnpm build`: PASS
- `pnpm lint`: PASS (0 errors)
- 라우트: 14/14 PASS
- UI/UX 기능: 10/10 PASS
- 다크모드: PASS
