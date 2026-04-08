# CHECKPOINT — Moldy Agent Builder v2

## M1: 아키텍처 설계 + 삭제 분석
- [ ] docs/ 구조 업데이트 (ARCHITECTURE.md에 Builder/Assistant 추가)
- [ ] Builder/Assistant 인터페이스 설계 (API 계약, 스키마, 디렉토리 구조)
- [ ] 삭제 대상 식별 (creation_agent.py, fix_agent.py 의존성 분석)
- 검증: `ls docs/ARCHITECTURE.md && ls tasks/deletion-analysis-v2.md`
- done-when: 아키텍처 문서 업데이트 + 삭제 분석 보고서 존재
- 상태: pending

## M2: Builder 백엔드
- [ ] LangGraph 오케스트레이터 + BuilderState + 4 서브에이전트
- [ ] Builder 전용 도구 + 파일 매니저
- [ ] Builder API 라우터 + 서비스 + 스키마
- 검증: `cd backend && uv run ruff check app/agent_runtime/builder/ app/routers/builder.py app/services/builder_service.py app/schemas/builder.py`
- done-when: ruff 에러 0
- 상태: pending

## M3: Assistant 백엔드
- [ ] Assistant 도구 32개 (read 16 + write 15+ + clarify 1)
- [ ] Assistant 에이전트 설정 (docs/fix_agent_assistant_prompt.md 로드)
- [ ] Assistant API 라우터 + 서비스 + 스키마
- 검증: `cd backend && uv run ruff check app/agent_runtime/assistant/ app/routers/assistant.py app/services/assistant_service.py app/schemas/assistant.py`
- done-when: ruff 에러 0
- 상태: pending

## M4: 프론트엔드
- [ ] Builder 7단계 파이프라인 UI (기존 conversational 교체)
- [ ] Assistant 대화 패널 UI
- [ ] API 클라이언트 (builder.ts, assistant.ts)
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공, 타입 에러 0
- 상태: pending

## M5: 통합 + E2E
- [ ] 기존 코드 교체 (creation_agent.py, fix_agent.py 삭제, 라우터 정리)
- [ ] main.py에 새 라우터 등록
- [ ] E2E 시나리오 검증
- 검증: `cd backend && uv run ruff check . && cd ../frontend && pnpm build`
- done-when: 린트 통과, 빌드 성공
- 상태: pending
