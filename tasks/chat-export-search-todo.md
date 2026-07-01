# G5 대화 Export + G6 대화 내 검색

브랜치: `feature/chat-export-search` (worktree). 순서: G5 → G6, 각각 별도 커밋.

## 공통
- 데이터 소스: `useMessagesEnvelope(conversationId)` → `envelope.messages` (전체 메모리 로드, 페이지네이션·가상화 없음). 백엔드 변경 불필요.

## G5 — 대화 Export (share와 대칭)
- [ ] `lib/chat/conversation-export.ts` (신규): `conversationToMarkdown(messages, opts)` / `conversationToJson(envelope)` / `downloadTextFile(content, filename, mime)`. 순수 함수, 라벨은 파라미터(i18n은 호출부).
- [ ] `ExportDialog(conversationId)` (신규, share-dialog 패턴): `useMessagesEnvelope`로 fetch → 포맷(Markdown/JSON) 선택 → 다운로드. DialogShell 사용.
- [ ] `use-conversation-row-actions.tsx`: `openExportDialog` + `exportTarget` state + dialogs에 ExportDialog (share 패턴 그대로).
- [ ] `chat-navigator-session-row.tsx`: 세션 메뉴에 "내보내기" DropdownMenuItem (공유 다음).
- [ ] i18n (ko/en) + 유틸 단위 테스트.

## G6 — 대화 내 검색 (Ctrl+F 오버레이)
- [ ] 클라이언트 in-memory 검색: `message.content` 대소문자 무시 필터 → 매치 메시지 id 목록.
- [ ] `jumpToMessage(messageId)` 재사용 (jump-to-message.tsx) + `moldy-jump-highlight`.
- [ ] Ctrl+F 오버레이 컴포넌트: 검색 입력 + "N/M" 카운트 + 이전/다음 + 닫기(Esc). 대화 뷰포트 상단.
- [ ] 키보드: Cmd/Ctrl+F 토글, Enter/Shift+Enter 이동, Esc 닫기.
- [ ] i18n (ko/en) + 검색 필터 단위 테스트.

## 검증 (각 단계)
- [ ] tsc / eslint / lint:i18n / lint:design-system / vitest
- [ ] E2E (export 다운로드, 검색 점프) — 판단 후
