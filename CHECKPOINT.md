# CHECKPOINT — M3: 스킬 + 메모리 전환

## M3-S1: 삭제 분석
- [x] M3 scope 내 제거 대상 코드 상세 분석 보고서
- 검증: `test -f tasks/m3-deletion-analysis.md`
- done-when: 삭제 분석 보고서 존재, 참조 그래프 + 삭제 순서 명시
- 담당: 베조스
- 상태: done

## M3-S2: 아키텍처 설계 + ADR-003
- [x] FilesystemBackend 선택 (virtual_mode=True)
- [x] skills 경로 매핑 패턴 (/skills/ 단일 소스)
- [x] memory 경로 패턴 (/agents/{agent_id}/AGENTS.md)
- [x] build_agent() 시그니처 변경 설계
- [x] ADR-003 기록 + docs/ARCHITECTURE.md 업데이트
- 검증: `test -f docs/design-docs/adr-003-skills-memory.md`
- done-when: ADR 존재 + backend/skills/memory 설계가 문서화됨
- 담당: 피차이
- 상태: done

## M3-S3: skills/memory 파라미터 연결
- [x] build_agent()에 skills/memory 파라미터 추가
- [x] execute_agent_stream()에서 agent_skills → skills 경로 리스트 변환
- [x] FilesystemBackend 설정 + create_deep_agent에 backend 전달
- [x] data/agents/{agent_id}/ 디렉토리 자동 생성 로직
- [x] memory 파라미터로 AGENTS.md 경로 전달
- 검증: `cd backend && uv run ruff check app/agent_runtime/executor.py && uv run pytest tests/test_executor.py`
- done-when: executor.py가 skills/memory를 create_deep_agent에 전달, ruff + 테스트 통과
- 담당: 젠슨
- 상태: done

## M3-S4: 코드 정리 (제거)
- [x] skill_tool_factory.py 제거
- [x] skill_executor.py 제거
- [x] chat_service.py: build_effective_prompt()에서 스킬 주입 로직 제거
- [x] chat_service.py: build_tools_config()에서 skill_package 변환 로직 제거
- [x] executor.py: skill_package 도구 생성 로직 제거
- [x] 관련 테스트 수정
- 검증: `cd backend && uv run ruff check . && grep -r "skill_tool_factory\|skill_executor\|create_skill_tools" app/`
- done-when: 제거된 파일/함수 참조 0건, ruff 통과
- 담당: 젠슨
- 상태: done

## M3-S5: 통합 검증
- [x] ruff check 전체 통과
- [x] pytest 308 passed, 0 failed
- [x] S1 삭제 체크리스트 18/18 PASS
- [x] skills/memory 관련 grep 검증 — 잔여 참조 0건
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: lint 통과, 테스트 통과, 삭제 분석 대조 완료
- 담당: 베조스
- 상태: done
