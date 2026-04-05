# CHECKPOINT — M1: Deep Agent 엔진 교체

## M1-S0: docs/ + ARCHITECTURE.md 초기화
- [x] ARCHITECTURE.md 생성 (현재 아키텍처 맵)
- 검증: `test -f docs/ARCHITECTURE.md`
- done-when: docs/ARCHITECTURE.md 존재
- 담당: 피차이
- 상태: done

## M1-S1: 삭제 분석
- [x] M1 scope 내 제거 대상 코드 상세 분석 보고서
- 검증: `test -f tasks/m1-deletion-analysis.md`
- done-when: 삭제 분석 보고서 존재, SPEC.md 제거 목록과 대조 완료
- 담당: 베조스
- 상태: done

## M1-S2: 아키텍처 설계 (API 계약)
- [x] create_deep_agent + langchain-mcp-adapters API 리서치
- [x] executor.py 새 설계도 (함수 시그니처, 호출 흐름)
- [x] ADR 기록
- 검증: `test -f docs/design-docs/adr-001-deep-agent-engine.md`
- done-when: ADR 존재 + executor.py 설계도가 피차이/젠슨 합의됨
- 담당: 피차이
- 상태: done

## M1-S3: 의존성 설치 + executor.py 교체
- [x] `uv add deepagents langchain-mcp-adapters`
- [x] build_agent() -> create_deep_agent() 교체
- [x] MCP 도구 생성 -> langchain-mcp-adapters 전환
- 검증: `cd backend && uv run ruff check app/agent_runtime/executor.py`
- done-when: executor.py가 create_deep_agent 사용, import 성공, ruff 통과
- 담당: 젠슨
- 상태: done

## M1-S4: 제거 대상 코드 정리
- [x] tool_factory.py: create_mcp_tool(), _build_args_schema() 제거
- [x] mcp_client.py: call_mcp_tool() 제거
- [x] chat_service.py: MCP 이름 가공/중복 감지 로직 제거
- 검증: `cd backend && uv run ruff check .`
- done-when: 제거 대상 함수 없음, ruff 통과, 나머지 코드에서 제거된 함수 참조 없음
- 담당: 젠슨
- 상태: done

## M1-S5: 통합 검증
- [x] ruff check 전체 통과
- [x] pytest — 314 passed, 9 failed (pre-existing, M1 무관)
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: lint 통과, M1 관련 테스트 109개 전체 통과, regression 없음
- 담당: 베조스
- 상태: done
