# CHECKPOINT — Moldy UI/UX 개선

## M0: docs/ 초기화 + 삭제 분석
- [ ] docs/ 디렉토리 구조 생성 + ARCHITECTURE.md
- [ ] 삭제 분석 보고서 작성
- 검증: `ls docs/ARCHITECTURE.md && ls tasks/deletion-analysis.md`
- done-when: 두 파일 모두 존재
- 상태: pending

## M1: UX 디자인 스펙 + 기반 구현
- [ ] 팀쿡 디자인 스펙 완료
- [ ] 삭제 확인 다이얼로그 (tools, skills, settings 트리거)
- [ ] 도구 상세 Sheet→Dialog 변경 + stopPropagation
- [ ] 에이전트 카드 개선 (도구목록→배지, 설명 강조, 호버 버튼)
- [ ] Coming Soon 패턴 적용 (chat-input, toolbar)
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공, 타입 에러 0
- 상태: pending

## M2: 네비게이션 + 레이아웃 개선
- [ ] 브레드크럼 컴포넌트 + 헤더 통합 + Separator 제거
- [ ] 사이드바 개선 (설정/로그아웃 연결, 테마 토글 분리)
- [ ] 앱 설정 페이지 신규 생성
- [ ] 모바일 채팅 대화목록 Sheet
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공, 모든 라우트 접근 가능
- 상태: pending

## M3: 페이지별 UX 개선
- [ ] 설정 페이지 탭 분리 + sticky 저장 + shadcn 교체 + 미저장 경고
- [ ] 대화형 생성 UX (취소, Timeline Phase1, 리셋, 로딩 텍스트)
- [ ] 사용량 페이지 (기간 선택기, Agent 링크)
- [ ] 스킬 페이지 (검색, 타입 필터)
- [ ] 기타 Minor (agents/new 뒤로가기, 채팅 헤더 모델명, models flex-wrap)
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드 + 린트 통과
- 상태: pending

## M4: 최종 검증
- [ ] 전체 빌드 + 린트 통과
- [ ] agent-browser로 모든 페이지 E2E 확인
- [ ] 다크모드/라이트모드 확인
- [ ] QUALITY_SCORE.md 작성
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 모든 게이트 통과 + E2E 확인 완료
- 상태: pending
