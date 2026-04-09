# CHECKPOINT — Builder Sub-Agent Prompt Improvement

## M1: prompt_generator.py SYSTEM_PROMPT 재설계
- [x] SYSTEM_PROMPT를 Fix Agent 수준의 구조적 템플릿으로 개선 (9개 필수 섹션, 7개 품질 기준, 금지 패턴)
- [x] _build_task_description에 도구/미들웨어 reason 필드 포함
- [x] _format_tools, _format_middlewares에 reason 포함
- 검증: `cd backend && uv run ruff check app/agent_runtime/builder/sub_agents/prompt_generator.py`
- done-when: ruff 에러 0, SYSTEM_PROMPT가 9개 이상 필수 섹션 가이드 포함
- 상태: done

## M2: 나머지 3개 sub-agent SYSTEM_PROMPT 보강 + SSE 메시지 개선
- [x] intent_analyzer.py: 객관성 원칙, 도메인별 기본값 5종, description 품질 기준, use_cases 작성 기준
- [x] tool_recommender.py: 사용자 관점 reason, 유사 도구 차별화 규칙
- [x] middleware_recommender.py: TodoList reason 강화, 시너지/충돌 규칙, reason 작성 기준
- [x] orchestrator.py: SSE 메시지 13건 사용자 친화적으로 변경
- 검증: `cd backend && uv run ruff check app/agent_runtime/builder/`
- done-when: ruff 에러 0, 4개 파일 모두 SYSTEM_PROMPT 보강됨
- 상태: done

## M3: "Deep Agent" → "Moldy Agent" 네이밍 통일
- [x] docs/fix_agent_assistant_prompt.md: "Deep Agent Assistant" → "Moldy Agent Assistant"
- [x] backend/app/agent_runtime/assistant/assistant_agent.py: fallback 텍스트 변경
- [x] backend/app/agent_runtime/executor.py: docstring 변경
- [x] backend/tests/test_assistant_agent.py: assert 문자열 변경
- 검증: `grep -r "Deep Agent" backend/app/ docs/fix_agent_assistant_prompt.md`
- done-when: grep 결과 0건
- 상태: done

## M4: 최종 통합 검증
- [x] Backend ruff 전체 통과
- [x] Backend pytest 39 passed (53.59s)
- [x] "Deep Agent" 참조 0건
- [x] Sub-agent SYSTEM_PROMPT 4개 모두 개선 반영 확인
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/test_builder_service.py tests/test_builder_service_stream.py tests/test_assistant_agent.py -v`
- done-when: 모든 검증 통과
- 상태: done
