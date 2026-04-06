# HANDOFF — M4: 정리 + Creation Agent

## 변경 사항 요약

- **creation_agent.py**: `model.ainvoke()` → `build_agent(tools=[])` + `agent.ainvoke()`. 모든 LLM 호출이 `create_deep_agent` 경로를 통과하는 통일된 엔진 달성.
- **trigger_executor.py**: `execute_agent_stream()` SSE 파싱 → `execute_agent_invoke()` 직접 호출. SSE 인코딩/디코딩 왕복 제거.
- **executor.py**: `_prepare_agent()` 추출으로 에이전트 빌드 로직 단일화. `execute_agent_invoke()` 추가 (트리거용 비스트리밍 실행).
- **streaming.py / middleware_registry.py**: 유지 (ADR-004). 유지 사유 주석 추가.
- **token_tracker.py**: 삭제 (미사용 dead code).
- **template.py**: `recommended_tools` 타입 힌트 `dict` → `list` 수정.

## 아키텍처 결정

- **ADR-004**: creation agent 최소 전환 (checkpointer 미사용), trigger invoke 공용화, streaming/middleware 유지
- **S1→S2 판단 수정**: streaming.py 미들웨어 필터 — S1 "삭제 가능" → S2 "유지 필요" (deepagents 소스 확인)

## 삭제된 항목 (Musk Step 2)

- `token_tracker.py` + `test_token_tracker.py`: 프로덕션 코드에서 참조 0건
- trigger_executor SSE 파싱 로직 11줄: `execute_agent_invoke`로 대체
- creation_agent `model.ainvoke()` 직접 호출: `build_agent` + `ainvoke`로 대체

## Ralph Loop 통계

- 총 스토리: 7개
- 1회 통과: 7개
- 재시도 후 통과: 0개
- 에스컬레이션: 0개

## 테스트 변화

- M3: 308 passed → M4: 302 passed
- 감소 6건: token_tracker 테스트 삭제
- regression: 0건

## 남은 작업

- [ ] (Phase 6) E2E 시나리오 검증 — Docker 환경에서 creation agent → 에이전트 생성 → 대화 → 트리거 실행
- [ ] creation_agent에 `response_format` 도입 검토 (프론트엔드 변경과 함께)
- [ ] deepagents 스트림 필터링 기능 추가 시 streaming.py 필터 제거
- [ ] langchain LLMToolSelectorMiddleware `{"const"}` 수정 시 패치 제거
- [ ] creation_agent에 도구 추가 (템플릿 검색, 스킬 카탈로그 브라우징)

## 배운 점 (progress.txt에서 발췌)

- 삭제 분석(S1)과 아키텍처 심층 조사(S2)가 상반된 결론을 낼 수 있음. 표면적 분석만으로 삭제 판단하지 말고 실제 소스코드(실행 경로)까지 추적할 것.
- `create_deep_agent(tools=[])` — 빈 도구 리스트로 호출해도 정상 동작. 단순 LLM 호출에도 deep agent 엔진을 통일할 수 있음.
- `_prepare_agent()` 추출로 stream/invoke 두 경로를 공용화하면 향후 배치 실행 등 새 실행 모드 추가가 용이.
