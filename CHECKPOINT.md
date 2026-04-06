# CHECKPOINT — M4: 정리 + Creation Agent

## M4-S1: 삭제 분석
- [x] M4 scope 내 제거 대상 코드 상세 분석
- [x] streaming.py 미들웨어 JSON 필터 필요성 분석
- [x] middleware_registry.py PatchedLLMToolSelectorMiddleware 필요성 분석
- [x] creation_agent.py 현재 구조 분석 (deep agent 전환 영향도)
- [x] trigger_executor.py 현재 구조 분석 (invoke 전환 영향도)
- 검증: `test -f tasks/m4-deletion-analysis.md`
- done-when: 삭제 분석 보고서 존재, 각 항목별 삭제/유지 판단 근거 명시
- 담당: 베조스
- 상태: done

## M4-S2: 아키텍처 설계 + ADR-004
- [x] creation_agent → create_deep_agent 전환 설계
- [x] trigger_executor → direct invoke() 전환 설계
- [x] streaming/middleware 정리 방향 설계
- [x] ADR-004 기록
- 검증: `test -f docs/design-docs/adr-004-m4-cleanup.md`
- done-when: ADR 존재, creation/trigger 전환 설계 문서화
- 담당: 피차이
- 상태: done

## M4-S3: trigger_executor → direct invoke
- [x] _prepare_agent() 추출 + execute_agent_invoke() 추가
- [x] SSE 파싱 로직 제거 → execute_agent_invoke 1줄 호출
- [x] 기존 테스트 수정 (10 passed)
- 검증: `cd backend && uv run ruff check app/agent_runtime/trigger_executor.py && uv run pytest tests/test_trigger_executor.py`
- done-when: trigger_executor가 invoke() 사용, ruff + 테스트 통과
- 담당: 젠슨
- 상태: done

## M4-S4: creation_agent → create_deep_agent
- [x] run_creation_conversation()을 build_agent(tools=[]) + agent.ainvoke() 기반으로 교체
- [x] 기존 테스트 수정 (10 + 18 passed)
- 검증: `cd backend && uv run ruff check app/agent_runtime/creation_agent.py && uv run pytest tests/test_creation_agent.py`
- done-when: creation_agent가 create_deep_agent 사용, ruff + 테스트 통과
- 담당: 젠슨
- 상태: done

## M4-S5: streaming/middleware 정리
- [x] ADR-004 결정: 둘 다 유지 (코드 로직 변경 없음, 주석만 추가)
- [x] streaming.py: _is_tool_selector_json + 버퍼링에 ADR-004 유지 사유 주석
- [x] middleware_registry.py: PatchedLLMToolSelectorMiddleware에 유지 사유 주석
- 검증: `cd backend && uv run ruff check app/agent_runtime/streaming.py app/agent_runtime/middleware_registry.py && uv run pytest tests/test_streaming.py`
- done-when: streaming/middleware 정리 완료, ruff + 테스트 통과
- 담당: 젠슨
- 상태: done

## M4-S6: 코드/시드 최종 정리
- [x] token_tracker.py + test_token_tracker.py 삭제
- [x] Template.recommended_tools 타입 힌트 dict → list 수정
- [x] 전체 ruff check 통과
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: 전체 lint 통과, 전체 테스트 통과
- 담당: 젠슨
- 상태: done

## M4-S7: 통합 검증
- [x] ruff check 전체 통과
- [x] pytest 302 passed, 0 failed
- [x] S1 삭제 체크리스트 18/18 PASS
- [x] 신규 코드 존재 확인 완료
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: lint 통과, 테스트 통과, 삭제 분석 대조 완료
- 담당: 베조스
- 상태: done
