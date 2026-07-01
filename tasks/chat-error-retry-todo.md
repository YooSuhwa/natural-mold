# G2 — 메인 채팅 에러 후 Retry

브랜치: `feature/chat-error-retry` (worktree)

## 설계 결론
- retry ≈ 실패 상태의 regenerate. 백엔드는 이미 checkpoint fork 재실행(`run.start`+`forkFrom`) 경로 보유 → **새 커맨드 불필요**.
- 실패 상태는 `latest_run.status="failed"`로 프론트에 도달. canceled와 동일 hydration 경로로 에러 버블 렌더.
- 에러 버블(합성 AIMessage)에 `ActionBarPrimitive.Reload` 재사용 → assistant-ui가 직전 user 메시지를 parentId로 넘김 → `checkpointForReload`가 마지막 user checkpoint fork → 재실행.

## 백엔드 ✅
- [x] `_run_metadata`에 `error_message`/`error_code` 노출 — **failed일 때만**(stale/canceled 계약 보존). `public_stream_error_message`로 마스킹된 안전 값.
- [x] scripted 모델에 `E2E_ERROR` 마커 추가 (invoke/stream 양쪽 raise → run.status=failed)

## 프론트 ✅
- [x] `terminal-notice.ts` 신규 (status 타입 + key 상수 + `terminalNoticeFromMessage`)
- [x] `ThreadRunNotice`에 `failed` + `errorMessage?`
- [x] `terminalRunNoticeFromThreadState`: failed 게이트 + error_message 추출
- [x] conversion: `attachTerminalNoticeMetadata` → `metadata.custom.terminalNotice`
- [x] `terminalNoticeText`: failed 분기(error_message 우선, 폴백 `chat.page.runFailed`)
- [x] `AssistantMsg`: failed면 `moldy-status-danger` 에러 버블 + AlertTriangle + 항상 보이는 RetryButton (hover metaRow 생략)
- [x] `RetryButton` (ActionBarPrimitive.Reload 재사용)
- [x] i18n: `chat.message.retry`, `chat.page.runFailed` (ko/en)

## 검증 ✅
- [x] typecheck / eslint / lint:i18n 통과
- [x] lint:design-system: 신규 코드 clean (11 이슈는 전부 pre-existing 기존 파일)
- [x] 전체 vitest 1149 통과 (terminal-notice 단위 + conversion 승격 + stream 훅 failed 감지 신규 테스트 포함)
- [x] backend ruff + 관련 pytest 통과 (stale 계약 회귀 수정 후 재통과), scripted 모델 E2E_ERROR 단위 테스트 추가

## 캡쳐 + E2E ✅
- [x] 캡쳐 spec `captures-chat-error-retry.spec.ts` (에러 버블 + retry 버튼 + retry 클릭 후)
- [x] E2E spec `chat-error-retry.spec.ts` (failed→에러 버블→retry 클릭→`/commands` POST 재실행 계약)
- [x] throwaway 스택(PG 5435 / 포트 3310·8310)으로 E2E 실행 → **통과** (DB에 conversation당 failed 런 2개 = retry 재실행 증거)
- [x] 캡쳐 실행(E2E_CAPTURE_TOUR=1) → PNG 3개 생성 (01 에러버블 / 02 element / 03 retry 후 재실행 로딩+중단)
- [x] 정리(throwaway PG 컨테이너 제거)

## 실행 중 발견·수정한 근본 버그 (retry no-op)
- 첫 E2E에서 retry 클릭이 `/commands`를 안 보냄 = no-op. 원인: 합성 에러 버블(AIMessage)이 `checkpointForReload`에서 "재생성 대상 assistant"로 오인돼 fork 대상 탐색이 null로 끝남(checkpoint-fork.ts:89).
- 수정: `terminal-notice.ts`에 `isTerminalNoticeMessageId` 추가 → fork context의 visible 메시지에서 합성 notice 제외 → user checkpoint로 정확히 fork. 회귀 단위 테스트 추가.
- 재실행 검증: E2E 통과 + 03 캡쳐가 재실행 로딩 상태를 보여줌.

## 최종 검증 ✅
- typecheck / eslint / lint:i18n / lint:design-system(신규 clean) / 전체 vitest **1151** / backend ruff+pytest / E2E 통과 / 캡쳐 PNG 3개

## 결정: E2E 필요성
- 핵심 로직(failed 감지, 에러 버블 승격, retry 배선)은 vitest로 커버.
- E2E는 "실제 브라우저에서 실패 런 → 에러 버블 렌더 + retry 클릭이 실제 재실행 커맨드를 발생"시키는 통합 계약을 검증 → **가치 있어 추가**. scripted 모델 `E2E_ERROR` 마커로 실제 실패 파이프라인 재현(run seed는 checkpoint 없어 retry no-op이 되므로 부적합).
