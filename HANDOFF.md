# HANDOFF — Moldy Agent Builder v2

## 변경 사항 요약

- **Builder 파이프라인**: 기존 단일 LLM 대화형(creation_agent.py) → LangGraph StateGraph 7단계 오케스트레이터 + 4개 서브에이전트(의도분석, 도구추천, 미들웨어추천, 프롬프트생성)
- **Moldy Assistant**: 기존 JSON 파싱 기반(fix_agent.py) → DeepAgents 도구 기반 에이전트(35개 도구, VERIFY-MODIFY 루프)
- **프론트엔드**: 대화형 생성 UI → 7단계 파이프라인 모니터링 UI (SSE 실시간), fix-agent Dialog → 설정 페이지 내 Assistant 탭

## 아키텍처 결정 (docs/design-docs/adr-005-builder-assistant.md)

- **AD-1**: Builder를 LangGraph StateGraph로 구현 (순차 파이프라인, 에러 라우팅)
- **AD-2**: 서브에이전트를 create_chat_model + ainvoke 직접 사용 (deep_agent 오버헤드 불필요)
- **AD-3**: Assistant 도구가 DB를 직접 수정 (confirm 불필요, 즉시 반영)
- **AD-4**: Builder API는 2단계 (POST start → SSE stream → POST confirm)
- **AD-5**: BuilderSession DB 모델로 중간 상태 영속화 (파일 기반 대신 DB JSON)
- **AD-6**: Assistant는 기존 대화 인프라(checkpointer, streaming) 재사용
- **AD-7**: 서브에이전트에 실제 도구/미들웨어 카탈로그 동적 주입

## 삭제된 항목 (Musk Step 2)

| 삭제 파일 | 교체 대상 |
|-----------|----------|
| agent_runtime/creation_agent.py | builder/orchestrator.py |
| agent_runtime/fix_agent.py | assistant/assistant_agent.py |
| routers/agent_creation.py | routers/builder.py |
| routers/fix_agent.py | routers/assistant.py |
| services/agent_creation_service.py | services/builder_service.py |
| schemas/agent_creation.py, fix_agent.py | schemas/builder.py, assistant.py |
| models/agent_creation_session.py | models/builder_session.py |
| lib/api/creation-session.ts | lib/api/builder.ts |
| components/agent/fix-agent-dialog.tsx | components/agent/assistant-panel.tsx |

## 신규 파일 (22개)

**Backend 16개**: builder/ (오케스트레이터 + 서브에이전트 4개), assistant/ (에이전트 + 도구 3개), models/builder_session.py, routers/builder.py, routers/assistant.py, services/builder_service.py, services/assistant_service.py, schemas/builder.py, schemas/assistant.py

**Frontend 6개**: lib/api/builder.ts, lib/api/assistant.ts, lib/sse/stream-builder.ts, lib/sse/stream-assistant.ts, components/agent/assistant-panel.tsx, conversational/page.tsx (교체)

## Ralph Loop 통계

- 총 스토리: 11개 | 1회 통과: 11개 | 재시도: 0 | 에스컬레이션: 0
- 팀: 피차이(아키텍처), 젠슨(백엔드), 팀쿡(디자인), 저커버그(프론트엔드), 베조스(QA)

## 검증 결과

- Backend ruff: PASS | pytest: 284 passed | Frontend build: PASS (18 pages) | lint: PASS

## 남은 작업

- [ ] v2 유닛 테스트 작성 (Builder 오케스트레이터, Assistant 도구, 라우터/서비스)
- [ ] Alembic 마이그레이션 (builder_sessions 테이블)
- [ ] E2E 실제 동작 검증 (LLM API 키 필요)
- [ ] /agents/new 페이지 "대화로 만들기" 라벨/경로 업데이트
- [ ] v1 관련 미사용 i18n 키 제거
