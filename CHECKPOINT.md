# CHECKPOINT — 채팅 UI 통합: assistant-ui 도입

## M1: 기반 설정 (패키지 + 어댑터 + Thread)
- [x] assistant-ui 패키지 설치 + pnpm build 통과
- [x] use-chat-runtime.ts + convert-message.ts 작성
- [x] assistant-thread.tsx 공통 Thread UI 작성
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공, 타입 에러 0
- 상태: done

## M2: 대화 페이지 마이그레이션
- [x] conversations/[conversationId]/page.tsx → Thread 전환
- [x] 기존 대화 기능 회귀 없음 (메시지 전송, 스트리밍, 토큰 표시)
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드+린트 통과
- 상태: done

## M3: AssistantPanel + Builder 마이그레이션
- [x] assistant-panel.tsx → Thread 전환
- [x] agents/new/conversational/page.tsx → Thread 래핑
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드+린트 통과
- 상태: done

## M4: 도구 UI + 위트 로딩 + 정리
- [x] tool-ui 컴포넌트 (generic fallback)
- [x] witty-loading.tsx (27개 메시지)
- [x] 기존 파일 삭제 4개 + chat-store atoms 정리
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드+린트 통과, 삭제 파일에 대한 import 참조 없음
- 상태: done
