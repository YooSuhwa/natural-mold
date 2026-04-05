# CHECKPOINT — M2: Checkpointer 전환

## M2-S1: 삭제 분석
- [x] M2 scope 내 제거 대상 코드 상세 분석 보고서
- 검증: `test -f tasks/m2-deletion-analysis.md`
- done-when: 삭제 분석 보고서 존재, SPEC.md 제거 목록과 대조 완료
- 담당: 베조스
- 상태: done

## M2-S2: 아키텍처 설계 + ADR
- [x] AsyncPostgresSaver 초기화 패턴 설계
- [x] 메시지 변환 로직 (LangChain BaseMessage → MessageResponse) 설계
- [x] auto-title 로직 이동 설계
- [x] ADR-002 기록 + docs/ARCHITECTURE.md 업데이트
- 검증: `test -f docs/design-docs/adr-002-checkpointer.md`
- done-when: ADR 존재 + checkpointer 초기화/메시지 변환/auto-title 설계가 문서화됨
- 담당: 피차이
- 상태: done

## M2-S3: AsyncPostgresSaver 설정 + executor 연결
- [x] lifespan에서 AsyncPostgresSaver 초기화 (pool 방식)
- [x] build_agent()에 checkpointer 전달
- [x] execute_agent_stream()에서 messages_history 로딩 제거 (checkpointer가 관리)
- 검증: `cd backend && uv run ruff check app/agent_runtime/executor.py app/agent_runtime/checkpointer.py app/main.py`
- done-when: executor.py가 checkpointer를 사용하여 에이전트 빌드, ruff 통과
- 담당: 젠슨
- 상태: done

## M2-S4: conversations router + chat_service 전환
- [x] GET /conversations/{id}/messages: checkpointer에서 state 추출 → MessageResponse 변환
- [x] POST /conversations/{id}/messages: save_message() 호출 제거, auto-title 로직 이동
- [x] chat_service.py: list_messages(), save_message() 제거
- [x] MessageResponse 스키마 유지 (FE 호환)
- 검증: `cd backend && uv run ruff check app/routers/conversations.py app/services/chat_service.py app/schemas/conversation.py`
- done-when: messages DB 의존성 완전 제거, API 응답 형식 유지, ruff 통과
- 담당: 젠슨
- 상태: done

## M2-S5: DB 마이그레이션 + 코드 정리
- [x] Alembic 마이그레이션: messages 테이블 DROP, token_usages.message_id → conversation_id FK
- [x] Message 모델 제거, TokenUsage 모델 FK 변경
- [x] Conversation.messages relationship 제거
- [x] 테스트 5파일 수정 (Message 참조 전부 제거)
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: 마이그레이션 성공, Message 모델 참조 0건, ruff 통과, 테스트 통과
- 담당: 젠슨
- 상태: done

## M2-S6: 통합 검증
- [x] ruff check 전체 통과
- [x] pytest 321 passed, 0 failed
- [x] S1 삭제 체크리스트 12/12 PASS
- [x] Alembic 마이그레이션 정합성 확인
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: lint 통과, 테스트 통과, 삭제 분석 대조 완료
- 담당: 베조스
- 상태: done
