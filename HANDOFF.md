# HANDOFF — 채팅 UI 통합: assistant-ui 도입

## 변경 사항 요약

- **채팅 프레임워크 교체**: 3곳(대화, AssistantPanel, Builder)의 독립 구현 → assistant-ui `useExternalStoreRuntime` 기반 통합
- **마크다운 렌더링 업그레이드**: react-markdown + react-syntax-highlighter → `@assistant-ui/react-streamdown` (Shiki + KaTeX)
- **위트 있는 로딩**: ThinkingDots ("...") → 27개 코믹 메시지 랜덤 로테이션 + 3-dot 애니메이션
- **코드 절감**: 대화 페이지 344→190줄, AssistantPanel 370→100줄, 삭제 4파일 ~1,000줄+

## 아키텍처 결정

- **ADR-006**: ExternalStoreRuntime 어댑터 선택 (LangGraph Cloud API 전환 대신 기존 SSE API 유지)
- **Thread 프리미티브 직접 조립**: shadcn Thread scaffold 미사용, 기존 UI 톤 유지
- **markdown-content.tsx 유지**: draft-config-card.tsx에서 아직 사용 중 → Builder 마크다운 렌더러 교체 시 함께 삭제
- **Builder는 Thread 래핑**: 대화형 Deep Agent 전환은 향후 별도 프로젝트

## 신규 파일

| 파일 | 설명 |
|---|---|
| `lib/chat/use-chat-runtime.ts` | ExternalStoreRuntime 어댑터 |
| `lib/chat/convert-message.ts` | Message → ThreadMessageLike 변환 |
| `lib/chat/use-builder-runtime.ts` | Builder SSE → 가상 메시지 변환 |
| `components/chat/assistant-thread.tsx` | 공통 Thread UI (~300줄) |
| `components/chat/tool-ui/generic-tool-ui.tsx` | 기본 도구 UI fallback |
| `components/chat/witty-loading.tsx` | 위트 로딩 (27개 메시지) |
| `agents/new/conversational/_components/builder-thread.tsx` | Builder 전용 Thread |
| `docs/design-docs/adr-006-assistant-ui-runtime.md` | ADR |

## 삭제된 항목 (Musk Step 2)

| 파일 | 이유 |
|---|---|
| `components/chat/streaming-message.tsx` | Thread 내장 스트리밍으로 대체 |
| `components/chat/message-bubble.tsx` | MessagePrimitive 커스텀으로 대체 |
| `components/chat/chat-input.tsx` | Composer로 대체 |
| `components/chat/tool-call-display.tsx` | makeAssistantToolUI로 대체 |
| `chat-store.ts` 스트리밍 atoms 4개 | 런타임이 상태 관리 |
| `lastMessageTokensAtom` | Dead code (쓰기만 하고 읽기 없음) |

## Ralph Loop 통계

- 총 스토리: 9개 (S0~S8)
- 1회 통과: 9개
- 재시도 후 통과: 0개
- 에스컬레이션: 0개

## 남은 작업

- [ ] **HiTL (Human-in-the-Loop)**: 백엔드 interrupt + resume 엔드포인트 + 프론트 ApprovalCard/UserInputUI (다음 PR)
- [ ] **tool-ui 도구 UI**: Plan, Progress Tracker, Citation, Code Block 등 (tool-ui 레포에서 복사, 다음 PR)
- [ ] **markdown-content.tsx 삭제**: Builder의 draft-config-card가 StreamdownText로 전환된 후
- [ ] **Builder 대화형 전환**: 파이프라인 → Deep Agent 기반 대화형 에이전트 생성 (별도 프로젝트)
- [ ] **markdown-styles.css 삭제 확인**: 참조 남아있는지 최종 확인

## 배운 점 (progress.txt 발췌)

- `ReadonlyJSONObject` (assistant-stream 타입) 직접 import 불가 → `Record<string,never>` 캐스팅으로 해결
- Thread 프리미티브 조립 시 기존 스타일 유지가 shadcn scaffold보다 유연
- ExternalStoreRuntime의 `onNew`에서 SSE AsyncGenerator 소비 패턴이 기존 handleSend 루프와 거의 동일 → 마이그레이션 비용 낮음
