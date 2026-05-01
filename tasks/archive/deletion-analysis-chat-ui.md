# 삭제 분석 보고서: 채팅 UI — assistant-ui 통합

**분석자**: bezos (QA Engineer)
**날짜**: 2026-04-09
**브랜치**: feature/chat-ui-assistant-ui
**스코프**: assistant-ui 도입 후 삭제/수정/유지 대상 분류

---

## 분석 대상 요약

| 디렉토리 | 파일 수 | 총 라인 |
|----------|---------|---------|
| components/chat/ | 6 + CSS 1 | ~1,069 + CSS |
| lib/stores/chat-store.ts | 1 | ~40 |
| components/agent/assistant-panel.tsx | 1 | 370 |
| agents/new/conversational/_components/ | 4 | ~392 |
| lib/sse/ | 4 | ~134 |

---

## 1. 즉시 삭제 가능 (assistant-ui 대체 완료 후)

### 1.1 components/chat/ — 삭제 대상 5개 + CSS 1개

| 파일 | 라인 | 대체 수단 | import 지점 | 비고 |
|------|------|-----------|------------|------|
| `streaming-message.tsx` | 78 | assistant-ui Thread streaming 렌더링 | conversation page (1곳) | 내부 ThinkingDots → 위트 로딩으로 대체 |
| `message-bubble.tsx` | 221 | assistant-ui Thread 메시지 렌더링 | conversation page (1곳) | **parseToolContent, normalizeContent 유틸 이동 필요** (아래 §3 참조) |
| `chat-input.tsx` | 140 | assistant-ui Composer | conversation page (1곳) | sessionTokenUsageAtom 읽기 → Composer에 토큰 표시 통합 |
| `tool-call-display.tsx` | 190 | makeAssistantToolUI | message-bubble (1곳), streaming-message (1곳) | 내부 전용. 부모 삭제 시 함께 삭제 |
| `markdown-content.tsx` | 190 | react-streamdown + Shiki + KaTeX | 4곳 (아래 상세) | **가장 많은 의존성 — 순서 주의** |
| `markdown-styles.css` | - | react-streamdown 자체 스타일 | markdown-content.tsx (1곳) | markdown-content와 함께 삭제 |

#### markdown-content.tsx 의존성 체인 (삭제 전 해소 필요)

```
markdown-content.tsx
├── message-bubble.tsx         → 삭제 대상 (동시 삭제 OK)
├── streaming-message.tsx      → 삭제 대상 (동시 삭제 OK)
├── assistant-panel.tsx        → S6에서 교체 (그때까지 유지 or 동시 교체)
└── draft-config-card.tsx      → S7에서 마크다운 렌더러 교체 필요
```

**삭제 순서**: S5(대화 페이지) → S6(AssistantPanel) → S7(Builder) 순으로 교체 후, 마지막에 markdown-content.tsx 삭제. 또는 S5에서 react-streamdown 기반 신규 마크다운 컴포넌트 생성 후, S6/S7에서 import 경로만 변경하고 기존 삭제.

### 1.2 chat-store.ts — 부분 삭제 (atoms 5개 중 3~4개)

| Atom | 카테고리 | Reader | Writer | 판정 |
|------|---------|--------|--------|------|
| `streamingMessageAtom` | 스트리밍 상태 | streaming-message.tsx | conversation page | **삭제** — assistant-ui 런타임이 대체 |
| `streamingToolCallsAtom` | 스트리밍 상태 | streaming-message.tsx | conversation page | **삭제** — assistant-ui 런타임이 대체 |
| `isStreamingAtom` | UI 상태 | streaming-message.tsx | conversation page | **삭제** — assistant-ui 런타임이 대체 |
| `sessionTokenUsageAtom` | 토큰 추적 | chat-input.tsx | conversation page | **유지** — 토큰 추적은 assistant-ui 외부 관심사 |
| `lastMessageTokensAtom` | 토큰 추적 | **없음 (dead code)** | conversation page | **삭제** — 쓰기만, 읽기 없음 |
| `StreamingToolCall` 타입 | 타입 | conversation page | - | **삭제** — assistant-ui 자체 타입으로 대체 |
| `TokenUsage` 타입 | 타입 | chat-input, conversation page | - | **유지** — sessionTokenUsageAtom과 함께 |

**결과**: chat-store.ts는 삭제하지 않고 `sessionTokenUsageAtom` + `TokenUsage`만 남긴다. 파일이 2개 export만 남으면 다른 store로 이동 검토.

### 1.3 assistant-panel.tsx — 전체 교체 (S6)

| 내부 컴포넌트 | 라인 | 대체 수단 | 중복 대상 |
|--------------|------|-----------|----------|
| `MessageBubble` (내부) | 81-131 | assistant-ui Thread 메시지 | chat/message-bubble.tsx와 **중복** (단순화 버전) |
| `ToolCallBadge` (내부) | 43-79 | makeAssistantToolUI | chat/tool-call-display.tsx와 **중복** (경량 버전) |

**이동 필요 로직**:
- SSE 이벤트 핸들링 (5종: content_delta, tool_call_start, tool_call_result, message_end, error) → useExternalStoreRuntime 어댑터로 이동
- `isComposingRef` (IME 조합 방지) → Composer에서 처리 or 커스텀 Composer에 통합
- `crypto.randomUUID()` 세션 관리 → 런타임 어댑터 레벨로 이동
- AbortController 관리 → 런타임 어댑터로 이동
- TanStack Query invalidation (`['agents']`) → 도구 실행 콜백으로 이동

---

## 2. 유지 항목

### 2.1 components/chat/ — 유지 1개

| 파일 | 라인 | 이유 |
|------|------|------|
| `conversation-list.tsx` | 250 | 사이드바 대화 목록. assistant-ui와 무관. TanStack Query + Next.js Router 기반. |

### 2.2 lib/sse/ — 전체 유지

| 파일 | 라인 | 이유 |
|------|------|------|
| `parse-sse.ts` | 70 | 공용 SSE 파서. 3개 스트림 모듈이 의존. |
| `stream-chat.ts` | 16 | useExternalStoreRuntime 어댑터에서 계속 사용 (백엔드 API 불변) |
| `stream-assistant.ts` | 21 | AssistantPanel 런타임 어댑터에서 계속 사용 |
| `stream-builder.ts` | 27 | Builder 페이지에서 계속 사용 |

### 2.3 lib/hooks/ — 유지

| 파일 | 이유 |
|------|------|
| `use-conversations.ts` | TanStack Query 훅. conversation-list + 대화 페이지에서 사용. |

### 2.4 conversational/_components/ — 유지 (이번 PR 스코프 외)

| 파일 | 라인 | 이유 |
|------|------|------|
| `phase-timeline.tsx` | 157 | Builder 전용. S7에서 Thread 래핑만 하고 내부 컴포넌트는 유지. |
| `intent-card.tsx` | 53 | Builder 전용. 유지. |
| `recommendation-card.tsx` | 50 | 범용 카드. 유지. |
| `draft-config-card.tsx` | 132 | Builder 전용. **markdown-content import → S7에서 신규 마크다운 렌더러로 교체 필요.** |

### 2.5 chat-store.ts — 부분 유지

- `sessionTokenUsageAtom` + `TokenUsage` 타입 유지

---

## 3. 이동 필요 유틸리티

### message-bubble.tsx 내부 유틸 함수 (삭제 전 추출)

| 함수 | 기능 | 이동 대상 |
|------|------|----------|
| `parseToolContent(content)` | Python dict + JSON 도구 결과 파싱 | `lib/utils/convert-message.ts` (신규) |
| `extractTextFromParsed(parsed)` | 파싱된 도구 결과에서 텍스트 추출 | `lib/utils/convert-message.ts` (신규) |
| `normalizeContent(content)` | 제어 문자 정규화 | `lib/utils/convert-message.ts` (신규) |

**이유**: 이 유틸들은 백엔드 메시지 → assistant-ui 메시지 변환 시 재사용될 가능성 높음. 특히 `parseToolContent`는 LangGraph 도구 결과 형식 파싱에 필수.

### streaming-message.tsx 내부 컴포넌트

| 컴포넌트 | 기능 | 판정 |
|---------|------|------|
| `ThinkingDots()` | 로딩 애니메이션 | **삭제** — 위트 로딩(3초 간격 랜덤 메시지)으로 대체 예정 |

---

## 4. 의존성 그래프

```
[삭제 대상]                      [유지 대상]
                                
streaming-message.tsx ──┐        conversation-list.tsx (독립)
                       │        
message-bubble.tsx ────┤        lib/sse/parse-sse.ts
  └─ parseToolContent  │          ├── stream-chat.ts (유지)
  └─ normalizeContent  │          ├── stream-assistant.ts (유지)
                       │          └── stream-builder.ts (유지)
chat-input.tsx ────────┤        
                       │        chat-store.ts
tool-call-display.tsx ─┤          └── sessionTokenUsageAtom (유지)
                       │          └── TokenUsage 타입 (유지)
markdown-content.tsx ──┤        
  └─ markdown-styles.css│       conversational/_components/ (전체 유지)
                       │          └── draft-config-card.tsx
assistant-panel.tsx ───┘              (markdown-content → 신규 렌더러로 교체)
  └─ MessageBubble (내부)
  └─ ToolCallBadge (내부)

chat-store.ts (부분 삭제)
  ├── streamingMessageAtom (삭제)
  ├── streamingToolCallsAtom (삭제)
  ├── isStreamingAtom (삭제)
  ├── lastMessageTokensAtom (삭제, dead code)
  └── StreamingToolCall 타입 (삭제)
```

---

## 5. 삭제 실행 순서 (권장)

| 순서 | 스토리 | 삭제/수정 대상 | 선행 조건 |
|------|--------|--------------|----------|
| 1 | S2 | message-bubble.tsx에서 parseToolContent 등 추출 → convert-message.ts | 없음 |
| 2 | S3-S4 | 신규 마크다운 렌더러 + 도구 UI 생성 | S2 완료 |
| 3 | S5 | streaming-message, message-bubble, chat-input, tool-call-display 삭제. chat-store.ts에서 스트리밍 atoms 삭제 | S3, S4 완료 |
| 4 | S6 | assistant-panel.tsx 전체 교체 | S3, S4 완료 |
| 5 | S7 | draft-config-card.tsx의 markdown-content import 교체 | S3 완료 |
| 6 | S5 이후 | markdown-content.tsx + markdown-styles.css 삭제 | S5, S6, S7 모두 완료 |
| 7 | S8 | lastMessageTokensAtom 삭제 확인, 미사용 import 정리, 빌드/린트 검증 | 전체 완료 |

---

## 6. 리스크 & 주의사항

| 리스크 | 심각도 | 설명 |
|--------|--------|------|
| markdown-content.tsx 조기 삭제 | HIGH | 4곳에서 import. S5/S6/S7 모두 완료 전에 삭제하면 빌드 실패 |
| parseToolContent 유실 | MEDIUM | message-bubble.tsx 삭제 시 유틸 함수도 함께 사라짐. 반드시 사전 추출 |
| sessionTokenUsageAtom 오삭제 | MEDIUM | chat-store.ts 정리 시 토큰 관련 atom까지 삭제하면 토큰 추적 깨짐 |
| draft-config-card.tsx 깨짐 | MEDIUM | markdown-content 삭제 후 Builder 페이지에서 마크다운 렌더링 불가 |
| lastMessageTokensAtom dead code | LOW | 현재 아무도 읽지 않음. 향후 사용 계획 없으면 삭제 |
| IME 조합 방지 로직 유실 | LOW | assistant-panel.tsx의 isComposingRef. CJK 입력 시 필요 |

---

## 7. 정량 요약

| 구분 | 파일 수 | 라인 수 (추정) |
|------|---------|---------------|
| 즉시 삭제 가능 | 7 (chat 5 + CSS 1 + assistant-panel 1) | ~1,189 |
| 부분 삭제 (atoms) | 1 (chat-store.ts 내 4 atoms + 1 타입) | ~20 |
| 유지 | 10 (conversation-list + sse 4 + hooks 1 + _components 4) | ~723 |
| 이동 필요 유틸 | 3 함수 (→ convert-message.ts) | ~30 |
| **총 삭제 라인** | | **~1,209** |
