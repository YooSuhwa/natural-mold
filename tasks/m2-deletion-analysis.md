# M2 삭제 분석 보고서

> 분석일: 2026-04-06
> 분석자: bezos (QA Engineer)
> 대상: M2 — Checkpointer 전환 scope

---

## 즉시 삭제 가능

### 1. `chat_service.py:list_messages()` (L61-70)
- **이유**: checkpointer에서 state["messages"]를 추출하는 방식으로 대체
- **외부 참조 3건**:
  - `routers/conversations.py:95` — `list_messages` 엔드포인트에서 호출
  - `routers/conversations.py:115` — `send_message` 엔드포인트에서 messages_history 로딩
  - `tests/test_chat_service.py:19` — import + 2개 테스트
- **판단**: 모든 호출처가 M2에서 수정 대상. 안전 삭제 가능.

### 2. `chat_service.py:save_message()` (L73-104)
- **이유**: checkpointer가 메시지 저장 처리. 수동 저장 불필요.
- **외부 참조 5건**:
  - `routers/conversations.py:109` — user message 저장
  - `routers/conversations.py:147` — assistant message 저장
  - `agent_runtime/trigger_executor.py:43` — trigger user message 저장
  - `agent_runtime/trigger_executor.py:84` — trigger assistant message 저장
  - `tests/test_chat_service.py:20` — import + 5개 테스트
- **⚠️ 주의**: auto-title 로직 (L92-102) 포함. 이 로직은 이동 필요 (아래 "이동 필요" 섹션 참조).

### 3. `models/conversation.py:Message` 클래스 (L35-51)
- **이유**: messages 테이블 제거 시 ORM 모델 불필요
- **외부 참조 7건**:
  - `models/__init__.py:4` — import + `__all__` export
  - `services/chat_service.py:13` — import (save_message/list_messages에서 사용)
  - `tests/test_conversations_router.py:12` — `_seed_message()` 헬퍼에서 직접 생성
  - `tests/test_trigger_executor.py:13` — `select(Message)` 검증 쿼리에서 사용
  - `tests/test_usage_service.py:12` — `_seed_usage()` 헬퍼에서 FK 충족용으로 생성
  - `tests/test_usage_router.py:12` — `_seed_agent_with_usage()` 헬퍼에서 FK 충족용으로 생성
- **판단**: 삭제 시 연쇄 수정 범위가 넓음. 정리 순서: token_usages FK 변경 → Message 제거.

### 4. `models/conversation.py:Conversation.messages` relationship (L30-32)
- **이유**: Message 클래스 제거 시 relationship도 제거 필요
- **외부 참조**: 코드에서 `conv.messages` 직접 접근은 발견되지 않음. cascade 설정만 존재.
- **판단**: 안전 삭제 가능.

---

## 삭제 후 수정 필요

### 5. `routers/conversations.py:list_messages` 엔드포인트 (L84-95)
- **현재**: DB 쿼리 (`chat_service.list_messages`)
- **변경**: checkpointer에서 state 추출 → MessageResponse 형태로 반환
- **수정 대상**: `routers/conversations.py`, 새 service 함수 필요
- **프론트엔드 API**: `GET /api/conversations/{id}/messages` 경로 유지, 응답 형태 변경 가능성 있음

### 6. `routers/conversations.py:send_message` 엔드포인트 (L98-157)
- **삭제 대상 코드**:
  - L109: `await chat_service.save_message(db, conversation_id, "user", data.content)` — checkpointer가 대체
  - L115-116: `messages = await chat_service.list_messages(...)` + `messages_history` 변환 — checkpointer가 히스토리 자동 관리
  - L147: `await chat_service.save_message(db, conversation_id, "assistant", full_content)` — checkpointer가 대체
- **유지 코드**: L118-119 (`build_effective_prompt`, `build_tools_config`), L121-137 (SSE 스트리밍)
- **수정 대상**: `routers/conversations.py`

### 7. `agent_runtime/trigger_executor.py` (L43, L50, L84)
- **삭제 대상 코드**:
  - L43: `await chat_service.save_message(db, conv.id, "user", trigger.input_message)` — checkpointer가 대체
  - L50: `messages_history = [{"role": "user", "content": trigger.input_message}]` — checkpointer 자동 관리
  - L84: `await chat_service.save_message(db, conv.id, "assistant", full_content)` — checkpointer가 대체
- **수정 대상**: `agent_runtime/trigger_executor.py` — deep agent invoke() 사용으로 변경 (M4 scope이지만, save_message 제거에 의존)
- **⚠️ 주의**: M4에서 trigger_executor 전체 교체 예정이나, M2에서 save_message 제거 시 trigger_executor도 동시에 수정 필요.

### 8. `models/token_usage.py:message_id` FK (L17)
- **현재**: `message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)`
- **변경**: `conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)` + `message_id` 제거
- **수정 대상**:
  - `models/token_usage.py` — FK 변경
  - `services/chat_service.py:save_token_usage()` (L107-128) — 파라미터 `message_id` → `conversation_id`
  - `tests/test_chat_service.py:219-228` — test_save_token_usage 수정
  - `tests/test_usage_service.py:60-74` — `_seed_usage()` 헬퍼에서 Message 생성 제거
  - `tests/test_usage_router.py:45-59` — `_seed_agent_with_usage()` 헬퍼에서 Message 생성 제거

### 9. `schemas/conversation.py:MessageResponse` (L34-43)
- **현재**: DB Message ORM에서 `from_attributes=True`로 자동 변환
- **변경**: checkpointer state에서 추출한 데이터 구조에 맞게 수정 필요
- **필드 변경 가능성**:
  - `id`: checkpointer message에는 UUID id가 없을 수 있음 → 인덱스 기반 생성 또는 제거
  - `tool_calls`, `tool_call_id`: LangChain 메시지 포맷에 맞게 변환 필요
- **프론트엔드 타입**: `frontend/src/lib/types/index.ts:168-176` (`Message` 인터페이스) — 백엔드 응답 구조와 1:1 대응. 변경 시 동기화 필수.

### 10. `models/__init__.py` — Message export (L4, L20)
- **수정**: `from app.models.conversation import Conversation, Message` → `from app.models.conversation import Conversation`
- **수정**: `__all__`에서 `"Message"` 제거

---

## 이동 필요 (삭제 아닌 재배치)

### 11. Auto-title 로직 (`chat_service.py:save_message` L92-102)
- **현재 위치**: `save_message()` 함수 내부
- **기능**: 첫 user 메시지로 대화 제목 자동 생성 ("새 대화" → 첫 메시지 내용)
- **이동 대상**: `send_message` 엔드포인트 또는 별도의 `auto_generate_title()` 서비스 함수
- **이동 이유**: checkpointer는 메시지만 저장. 대화 메타데이터(title) 관리는 여전히 conversations 테이블에서 해야 함.
- **구현 제안**:
  ```python
  async def auto_generate_title(db: AsyncSession, conversation_id: uuid.UUID, content: str):
      title = content.strip().replace("\n", " ")
      if len(title) > 40:
          title = title[:37] + "..."
      await db.execute(
          update(Conversation)
          .where(Conversation.id == conversation_id, Conversation.title == "새 대화")
          .values(title=title)
      )
      await db.commit()
  ```
- **호출 위치**: `conversations.py:send_message` (user message 전송 시), `trigger_executor.py` (trigger 실행 시)

---

## Alembic 마이그레이션 필요

### 12. 새 마이그레이션 파일 생성 필요
1. `token_usages` 테이블: `message_id` 컬럼 제거 + `conversation_id` FK 추가
2. `messages` 테이블: DROP TABLE
3. **순서 주의**: token_usages FK 변경 → messages 테이블 DROP (FK 의존성)
4. `PostgresSaver` 자동 테이블 (checkpoint, checkpoint_blobs, checkpoint_writes)은 LangGraph setup()이 처리

---

## 테스트 영향

### `tests/test_chat_service.py` — 7개 테스트 영향
| 테스트 | 영향 | 대응 |
|--------|------|------|
| `test_list_messages_ordering` | 직접 삭제 대상 | 삭제 또는 checkpointer 기반으로 재작성 |
| `test_list_messages_limit` | 직접 삭제 대상 | 삭제 또는 checkpointer 기반으로 재작성 |
| `test_save_message_user_generates_title` | auto-title 로직 이동 후 재작성 | 이동된 함수 테스트로 대체 |
| `test_save_message_user_long_title_truncated` | auto-title 로직 이동 후 재작성 | 이동된 함수 테스트로 대체 |
| `test_save_message_assistant_no_title_change` | auto-title 로직 이동 후 재작성 | 이동된 함수 테스트로 대체 |
| `test_save_token_usage` | message_id FK 변경 후 수정 | conversation_id 파라미터로 변경 |
| (save_message import) | import 제거 | list_messages/save_message import 제거 |

### `tests/test_conversations_router.py` — 5개 테스트 영향
| 테스트 | 영향 | 대응 |
|--------|------|------|
| `test_list_messages_empty` | 응답 구조 변경 가능 | checkpointer 기반 응답에 맞게 수정 |
| `test_list_messages_with_data` | `_seed_message()` 헬퍼가 Message ORM 사용 | 헬퍼 제거, checkpointer로 메시지 삽입 |
| `test_list_messages_conversation_not_found` | 영향 적음 | 유지 가능 |
| `test_send_message_saves_user_message` | save_message 제거 후 검증 방식 변경 | checkpointer에서 메시지 확인으로 변경 |
| `test_send_message_streaming` | mock 대상 변경 가능 | 검토 필요 |

### `tests/test_trigger_executor.py` — 3개 테스트 영향
| 테스트 | 영향 | 대응 |
|--------|------|------|
| `test_execute_trigger_content_parsing` (L242-270) | `select(Message)` 검증 쿼리 사용 | checkpointer 기반 검증으로 변경 |
| `test_execute_trigger_saves_user_message` (L336-361) | `select(Message)` 검증 쿼리 사용 | checkpointer 기반 검증으로 변경 |
| (Message import, L13) | import 제거 | `from app.models.conversation import Conversation` (Message 제거) |

### `tests/test_usage_service.py` — 전체 테스트 영향
| 테스트 | 영향 | 대응 |
|--------|------|------|
| 전체 (5개) | `_seed_usage()` 헬퍼가 Message 생성 + message_id FK 사용 | FK 변경 후 Message 생성 제거, conversation_id 직접 사용 |

### `tests/test_usage_router.py` — 전체 테스트 영향
| 테스트 | 영향 | 대응 |
|--------|------|------|
| 전체 (4개) | `_seed_agent_with_usage()` 헬퍼가 Message 생성 + message_id FK 사용 | FK 변경 후 Message 생성 제거, conversation_id 직접 사용 |

---

## 프론트엔드 영향

### API 응답 구조 변경 가능성
- `GET /api/conversations/{id}/messages` 응답 스키마가 변경될 경우:
  - `frontend/src/lib/types/index.ts:168-176` (`Message` 인터페이스) 수정 필요
  - `frontend/src/lib/api/conversations.ts:20-21` — API 클라이언트
  - `frontend/src/lib/hooks/use-conversations.ts:17-18` — TanStack Query hook
  - `frontend/src/app/agents/[agentId]/conversations/[conversationId]/page.tsx` — 채팅 페이지
- **권장**: 백엔드에서 기존 `MessageResponse` 스키마와 최대한 호환되는 형태로 변환하여 프론트엔드 변경 최소화

---

## 삭제/수정 순서 제안

1. **auto-title 로직 추출** (`save_message` → 별도 함수)
2. **token_usages FK 변경** (`message_id` → `conversation_id`) + Alembic 마이그레이션
3. **conversations router 수정** (checkpointer 기반 list_messages, save_message 호출 제거)
4. **trigger_executor 수정** (save_message 호출 제거)
5. **chat_service.py 정리** (`save_message`, `list_messages` 삭제)
6. **Message 모델 제거** + `models/__init__.py` 정리
7. **messages 테이블 DROP** Alembic 마이그레이션
8. **테스트 전면 수정**
9. **프론트엔드 타입 동기화** (필요 시)

---

## 요약 통계

| 분류 | 항목 수 |
|------|---------|
| 즉시 삭제 가능 | 4건 |
| 삭제 후 수정 필요 | 6건 |
| 이동 필요 | 1건 (auto-title) |
| 영향받는 테스트 파일 | 5개 (약 19개 테스트) |
| 영향받는 프론트엔드 파일 | 4개 (타입 변경 시) |
| Alembic 마이그레이션 | 1개 (token_usages FK 변경 + messages DROP) |
