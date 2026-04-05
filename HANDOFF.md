# HANDOFF — M3: 스킬 + 메모리 전환

## 변경 사항 요약

- `create_deep_agent(skills=["/skills/"], memory=["/agents/{id}/AGENTS.md"])` 네이티브 파라미터 연결
- `FilesystemBackend(root_dir=data/, virtual_mode=True)` 설정
- `skill_tool_factory.py`, `skill_executor.py` 전체 삭제 (~217줄)
- `chat_service.py`: `build_effective_prompt()` 단순화, `build_tools_config()` skill_package 제거, `get_agent_skill_contents()` 삭제
- `conversations.py`, `trigger_executor.py`: agent_skills/agent_id 전달
- 테스트: 13건 삭제 (삭제된 코드), TestPromptInjection 수정, 2건 추가 (skills/memory 전달 검증)

## 아키텍처 결정

- **ADR-003**: `docs/design-docs/adr-003-skills-memory.md`
- **FilesystemBackend 단일 사용**: CompositeBackend/StoreBackend 대신 단순성 선택
- **skills=["/skills/"] 단일 소스**: per-agent 필터링 없음 (PoC). 프로그레시브 디스클로저로 보완
- **memory=["/agents/{id}/AGENTS.md"]**: MemoryMiddleware가 파일 미존재 시 graceful 무시
- **Text 스킬 물질화**: DB content → SKILL.md 디스크 기록은 미구현 (향후 필요 시)

## 삭제된 항목 (Musk Step 2)

| 항목 | 파일 | 이유 |
|------|------|------|
| `skill_tool_factory.py` (124줄) | 전체 파일 | deepagents SkillsMiddleware가 대체 |
| `skill_executor.py` (93줄) | 전체 파일 | 에이전트 빌트인 도구(execute)가 대체 |
| `get_agent_skill_contents()` | chat_service.py | SkillsMiddleware가 시스템 프롬프트 주입 담당 |
| `build_effective_prompt()` 스킬 로직 | chat_service.py | `return agent.system_prompt`로 단순화 |
| `build_tools_config()` skill_package 블록 | chat_service.py | skills 파라미터가 대체 |
| `skill_package` elif 분기 | executor.py | skills 파라미터가 대체 |
| `skill_script_timeout`, `skill_max_output_bytes` | config.py | 삭제된 코드 전용 설정 |
| `TestSkillExecutor` (7건), `TestSkillToolFactory` (6건) | test_skill_package.py | 삭제된 코드 테스트 |

## Ralph Loop 통계

- 총 스토리: 5개
- 1회 통과: 5개
- 재시도 후 통과: 0개
- 에스컬레이션: 0개

## 남은 작업

- [ ] Text 스킬 디스크 물질화 (`_materialize_skill_to_disk`) — 현재 text 스킬은 시스템 프롬프트에 주입되지 않음
- [ ] Per-agent 스킬 필터링 (FilteringFilesystemBackend 또는 custom middleware)
- [ ] 에이전트 생성 시 `data/agents/{id}/` 디렉토리 생성
- [ ] 메모리 자동 관리 (요약, 정리, 만료)
- [ ] M4: Creation Agent 교체 + Trigger 전환 + 최종 정리
- [ ] 실제 서버에서 스킬 호출 E2E 수동 검증

## 배운 점 (progress.txt 발췌)

- deepagents `_list_skills`는 source_path의 서브디렉토리를 스캔 → 개별 스킬 경로 직접 전달 불가, 부모 디렉토리 전달
- FilesystemBackend `virtual_mode=True`는 O_NOFOLLOW로 심볼릭 링크 차단 — per-agent 심볼릭 링크 전략 불가
- MemoryMiddleware는 파일 미존재 시 graceful 무시 (에러 없음) — 빈 디렉토리로 시작 가능
- 팀원 리스폰 시 progress.txt에서 컨텍스트 복원 가능 (젠슨 S3→S4 전환에서 확인)
