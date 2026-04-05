# HANDOFF — M2: Checkpointer 전환

## 변경 사항 요약

- DB messages 테이블 기반 대화 관리 → LangGraph AsyncPostgresSaver checkpointer 기반으로 전환
- `save_message()`, `list_messages()` 제거 — checkpointer가 메시지 저장/조회 자동 처리
- `Message` ORM 모델 + messages 테이블 완전 제거
- `token_usages` FK: `message_id` → `conversation_id`로 변경
- 프론트엔드 변경 없음 (백엔드가 기존 MessageResponse 형식 유지)

## 아키텍처 결정

- **ADR-002**: `docs/design-docs/adr-002-checkpointer.md`
- **Checkpointer 싱글턴** (`checkpointer.py`): 모듈-레벨 싱글턴, psycopg_pool 기반. executor + trigger_executor 모두 접근 가능
- **메시지 변환** (`message_utils.py`): `langchain_messages_to_response()` — LangChain BaseMessage → MessageResponse. created_at은 합성 타임스탬프
- **auto-title 분리**: `save_message()` 내부 → `maybe_set_auto_title()` 독립 함수
- **대화 삭제**: `delete_thread()` → checkpoint 3개 테이블 직접 SQL 삭제 → conversations 테이블 삭제

## 삭제된 항목 (Musk Step 2)

| 항목 | 파일 | 이유 |
|------|------|------|
| `save_message()` | chat_service.py | checkpointer가 대체 |
| `list_messages()` | chat_service.py | checkpointer가 대체 |
| `Message` ORM 클래스 | models/conversation.py | messages 테이블 제거 |
| `Conversation.messages` relationship | models/conversation.py | Message 제거에 따라 |
| `token_usages.message_id` FK | models/token_usage.py | `conversation_id` FK로 교체 |
| save_message 호출 2건 | trigger_executor.py | checkpointer가 대체 |
| messages 테이블 | Alembic 마이그레이션 | checkpointer가 대체 |

## 신규 파일/함수

- `backend/app/agent_runtime/checkpointer.py` — init/shutdown/get/delete_thread 싱글턴
- `langchain_messages_to_response()` in `message_utils.py` — BaseMessage → MessageResponse
- `maybe_set_auto_title()` in `chat_service.py` — 대화 제목 자동 생성
- Alembic 마이그레이션: messages DROP + token_usages FK 변경

## Ralph Loop 통계

- 총 스토리: 6개
- 1회 통과: 6개
- 재시도 후 통과: 0개
- 에스컬레이션: 0개

## 남은 작업

- [ ] M3: 스킬 + 메모리 전환 (`create_deep_agent(skills=[...])`, `skill_tool_factory.py` 제거)
- [ ] M4: Creation Agent 교체 + Trigger 전환 + 최종 정리
- [ ] 실제 DB 환경에서 `alembic upgrade head` 실행 필요
- [ ] 실제 DB 환경에서 E2E 채팅 흐름 (브라우저) 검증 필요

## 배운 점 (progress.txt 발췌)

- `save_message()` 안에 auto-title 로직이 숨어있었음 — 삭제 분석 없이 제거했으면 기능 손실
- trigger_executor.py가 save_message를 호출 중이었음 — M4 scope이지만 M2에서 동시 수정 필수
- FK 순서 제약: token_usages FK 변경 → messages DROP 순서 위반 시 마이그레이션 실패
- checkpointer messages_history는 "새 메시지만" 전달 — 전체 히스토리 전달 시 중복 발생
- created_at 합성 타임스탬프로 프론트엔드 변경 제로 달성
- pre-existing 실패 9건이 M2에서 0으로 해결됨 (테스트 정리 과정에서 수정)
