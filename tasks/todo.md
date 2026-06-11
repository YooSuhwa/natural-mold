# chat-run-lifecycle 리뷰 후속 수정 (2026-06-11 전체 완료)

## 1·2차 수정 — 완료
- [x] 백엔드 major 4건 (cancel race, with_for_update, AG-UI messageId, broker_gap)
- [x] 프론트 major (streamInFlightRef/consumedRunIdRef 가드, unmount 토큰 무효화)
- [x] minor 전부 + tsc 클린

## 3차 잔여 항목 — 완료
- [x] ① P3.2 skill subprocess 취소 kill (skill_executor.py + 회귀 테스트, 수정 전 실패 실증)
- [x] ② P4 F5 refresh E2E 시나리오 — 실 브라우저+실 백엔드에서 통과
- [x] ③ P5.2 spend hook 회귀 테스트 3건
- [x] ④ 기획 문서 체크박스 19개 + Implementation Status and Deviations 섹션

## E2E 게이트 중 발견된 추가 결함 — 수정 완료
- [x] streamSSEPost abort deadlock (parse-sse.ts): fetch-event-source가 abort 시
      reject 대신 resolve → closed 미설정 → 소비 루프 영구 대기 → Stop 후
      isRunning 미해제. finally에서 AbortError 변환으로 수정.
      유닛 회귀 테스트(수정 전 타임아웃 실증) + P3 cancel E2E 첫 통과.

## 검증 (최종)
- [x] chat-run-lifecycle E2E 전체 6/6 통과 (P1 contract, P2 navigation, P3 cancel/stale/interrupted, P4 refresh)
- [x] backend: 관련 17 passed + ruff
- [x] frontend: vitest 전체 + lint + tsc + build (최종 라운드)

## 남은 후속 (플래그 PR로 이연)
- [ ] ag_ui 이중 프로토콜 E2E 게이트 + gap 시 합성 TEXT_MESSAGE_START
