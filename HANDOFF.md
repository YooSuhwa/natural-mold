# HANDOFF — 채팅 UI 안정화 + 시간 시스템 정착 (세션 5, 2026-04-28)

**Base**: `main @ 4f8df0c` (PR #76 머지 후)
**누적 변경**: ~30 파일 (backend 7 + frontend 16 + alembic m15 + docs/지원 5)
**검증 상태**: backend ruff + 624 pytest / frontend lint + format + 257 tests + build 모두 PASS
**이전 세션 기록**: 본 파일 위쪽 섹션(세션 1~3) + git log 참조

---

## 이번 세션 핵심 변경

### 1. 채팅 박스 카드 레이아웃 (Image #22)
- `page.tsx`: 루트 `bg-muted/30 + p-3 + gap-3`, 좌/우 각 `rounded-xl border bg-card shadow-sm` 카드
- 헤더 단순화: 제목 + ⋯ 드롭다운(새 대화/설정)
- `ConversationList`: 에이전트 카드 헤더 + "대화" 라벨 + 휴지통 풋터(`toast.info` placeholder)

### 2. 시간 시스템 (가장 까다로웠음)
- **백엔드**: `MessageResponse`/`ConversationResponse`에 `UtcDatetime` annotation(`PlainSerializer`로 'Z' suffix). `m15_add_message_timestamps` 마이그레이션 — `Conversation.message_timestamps: dict[msg_uuid, iso]` 영구 저장으로 옛 메시지 시각이 송신 시 흔들리지 않게.
- **프론트엔드**: `lib/utils/format-relative-time.ts` 신규 — `Intl.DateTimeFormat(timeZone='Asia/Seoul')` 직접 사용 (use-intl wrapper의 timeZone 옵션이 일관되지 않아 우회). `parseTimestamp`로 'Z' 없는 string은 UTC로 가정.

### 3. 채팅 streaming 버그 (오늘 진단/fix)
- **list-content fix** (`backend/app/agent_runtime/streaming.py`): Anthropic multi-block content가 `list[dict]`로 와도 처리. 이전엔 `isinstance(delta, str)`만 처리해 tool 사용 시 token streaming이 0개였음. 지금은 `content_to_text` 공유 헬퍼로 평탄화.
- **메시지 refetch 깜박임 fix** (`use-chat-runtime.ts`): `setStreamingMessages([])`를 `finally`에서 즉시 호출 → refetch 도착까지 답변 사라지는 깜박임. `prevMessagesRef` rendering-time 비교로 messages 변경 후 clear.
- **scroll fix** (`assistant-thread.tsx`): `ThreadPrimitive.Root`/`Viewport`에 `min-h-0` — 메시지 많을 때 입력창 화면 밖으로 밀려나는 문제.
- **streaming tool_call dedupe** (`streaming.py`): `_INTERNAL_TOOL_NAMES` filter(`ToolSelectionResponse` 등 미들웨어 schema 노출 차단) + `(name, id)` 기준 dedupe.

### 4. UI 디테일
- 사용자 메시지 wrapper `flex flex-col items-end max-w-[80%]` (짧은 메시지 우측 여백 fix)
- 메시지 hover 시만 시간/복사 표시 (`MessageMetaRow` 추출)
- AI 아바타 emerald 배경 + `imageUrl` 변경 시 hasError 자동 reset (`prevImageUrl` rendering-time 패턴)
- Composer: 모델 좌측, 토큰 바 `ml-auto` 우측 정렬, send 버튼 `variant="emerald"`
- StreamingLoadingIndicator를 absolute(`-top-5 left-11`)로 띄워 답변 텍스트 위치 stable
- `Button` cva에 `emerald`/`emeraldStrong` variant 추가
- 이미지 webp 변환 (3.6MB → 142KB, -96%)

---

## 다음에 해야 할 작업

| 우선순위 | 항목 | 영향/작업 |
|---|---|---|
| 1 | **커밋/PR** (4-5개 분리 커밋 권장: 박스 레이아웃 / 시간 시스템 / 이미지 / refactor) | 중/하 |
| 2 | `list_messages_from_checkpointer` write-on-read 제거 — LangGraph hook 또는 별도 messages 테이블 | 상/상 |
| 3 | 휴지통 실제 기능 — 소프트 삭제(`deleted_at`) + 복원 페이지 | 중/중 |
| 4 | StreamingLoadingIndicator `-top-5 left-11` 매직 숫자 → avatar `sizeMap` 동기화 | 하/중 |

## 주의사항
- **`AppSidebar`/`AppHeader`/`AppLayout`**: 손대지 말 것 (사용자 결정으로 글로벌 사이드바 유지).
- **휴지통**: 현재 `toast.info()` placeholder만. 실제 기능 미구현.
- **모델 드롭다운**: 사용자가 명시적으로 보류 (시각만 텍스트, 인터랙션 X).
- **flex-order DOM hack** (`OrderedTextPart`/`OrderedToolFallback`): 사용자 명시 요구로 도구→텍스트 순서. a11y 영향 인지하되 변경 시 큰 리팩토링.

## 핵심 파일
- `frontend/src/components/chat/assistant-thread.tsx` — 메시지/composer/streaming
- `frontend/src/lib/chat/use-chat-runtime.ts` — SSE 스트림 누적/state 전환
- `frontend/src/lib/utils/format-relative-time.ts` — KST 시간 라벨
- `backend/app/agent_runtime/streaming.py` — SSE event 변환
- `backend/app/services/chat_service.py` — `list_messages_from_checkpointer` (timestamp 영구 매핑)
- `backend/alembic/versions/m15_add_message_timestamps.py`

## 마지막 상태
- 브랜치: `main` (uncommitted)
- 변경 파일: ~30개 (커밋 대기)
- backend dev: PID 91482 (uvicorn --reload)
- frontend dev: background ID `bim0lrvw7` (port 3000)
- DB: `m15` 마이그레이션 적용됨

새 세션에서: "HANDOFF.md 읽고 커밋 진행해줘" 또는 "다음 작업 #2 진행"으로 이어가면 됩니다.
