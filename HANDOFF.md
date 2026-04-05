# HANDOFF — M1: Deep Agent 엔진 교체

## 변경 사항 요약

- `executor.py`: `create_react_agent` 폴백 패턴 → `create_deep_agent` 단일 호출로 교체
- `executor.py`: MCP 도구 생성을 `langchain-mcp-adapters` `MultiServerMCPClient`로 전환
- `tool_factory.py`: `create_mcp_tool()`, `_build_args_schema()` 제거 (51줄 감소)
- `mcp_client.py`: `call_mcp_tool()`, `_extract_text()` 제거 (17줄 감소)
- `chat_service.py`: MCP 이름 중복 감지/리네이밍 로직 제거
- `test_executor.py`: `build_agent()` 테스트 2개를 `create_deep_agent` mock으로 수정
- 의존성 추가: `deepagents==0.4.12`, `langchain-mcp-adapters==0.2.2`

## 아키텍처 결정

- **ADR-001**: `docs/design-docs/adr-001-deep-agent-engine.md`
- `create_deep_agent` 단일 호출, 폴백 없음 (실패 시 git revert)
- MCP 서버별 그룹화 → `MultiServerMCPClient` → `get_tools()` → 이름 필터링
- transport: `"streamable_http"` (ADR 원본에서 `"http"`로 기술 — 실제 구현에서 수정)
- `deepagents==0.4.12`에서 `skills`/`memory` 파라미터 존재 (피차이 리서치 시점의 이전 버전에는 없었음)

## SPEC 대비 차이

| SPEC 기술 | 실제 구현 | 비고 |
|-----------|----------|------|
| `create_deep_agent(skills=[...])` | 0.4.12에서 지원됨 | M3에서 사용 예정 |
| `create_deep_agent(memory=[...])` | 0.4.12에서 지원됨 | M3에서 사용 예정 |
| `middleware` 파라미터 | 호환 확인됨 | langchain 22종 그대로 전달 |

## 삭제된 항목 (Musk Step 2)

| 항목 | 파일 | 이유 |
|------|------|------|
| `create_mcp_tool()` | tool_factory.py | langchain-mcp-adapters가 대체 |
| `_build_args_schema()` | tool_factory.py | create_mcp_tool 내부 의존성 |
| `call_mcp_tool()` | mcp_client.py | 어댑터가 도구 실행 처리 |
| `_extract_text()` | mcp_client.py | call_mcp_tool 내부 의존성 |
| MCP 이름 가공 로직 | chat_service.py | 어댑터가 이름 관리 |
| create_react_agent 폴백 | executor.py | create_deep_agent 단일 호출 |

## Ralph Loop 통계

- 총 스토리: 6개
- 1회 통과: 6개
- 재시도 후 통과: 0개
- 에스컬레이션: 0개

## 남은 작업

- [ ] M2: Checkpointer 전환 (PostgresSaver, messages 테이블 제거)
- [ ] M3: 스킬 + 메모리 전환 (create_deep_agent skills/memory 파라미터 활용)
- [ ] M4: Creation Agent 교체 + 최종 정리
- [ ] Pre-existing 테스트 실패 9건 수정:
  - `test_streaming.py` 3건: 미들웨어 JSON 필터 테스트 업데이트 필요
  - `test_skill_package.py` 6건: skill 도구 이름 규칙 변경 반영 필요

## 배운 점 (progress.txt 발췌)

- `deepagents` 0.4.x에서 skills/memory 파라미터 추가됨 — SPEC 작성 시점과 구현 시점의 API가 다를 수 있으므로, 항상 최신 버전 확인
- MCP transport는 `"streamable_http"` (문서에 `"http"`로 표기되는 경우 주의)
- MCP 도구 삭제 순서: leaf → stem (참조 없는 것부터 제거하면 중간 breakage 없음)
- M1 관련 테스트 109개 / 비관련 9개 실패는 pre-existing — git stash로 확인하는 습관
