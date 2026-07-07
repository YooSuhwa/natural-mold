# CHECKPOINT — 스킬 스튜디오 Phase 1: 스킬 빌더 챗

> 이전 내용(마켓플레이스 Phase 1)은 완료·머지되어 교체함 (ADR-017 출시 완료).

스펙: `docs/design-docs/skill-studio-phase1-builder-chat-spec.md` (커밋 5f730cd6)
브랜치: `feature/skill-builder-chat` (worktree `.claude/worktrees/feature+skill-builder-chat`)
원칙: 마일스톤 완료마다 커밋. push 검증 시 `SKILL_EVALUATION_ENABLED=true`.

## M1: 히든 빌더 에이전트 + 세션 v2
- [x] 마이그레이션 2종: `agents.runtime_profile`(default 'standard', m67), `skill_builder_sessions`에 `conversation_id` FK + `draft_workspace_path` (+`tool_consents` JSON) (m68)
- [x] **노출 표면 전수 grep**: 에이전트 목록/요약/대시보드/일일 집계/네비게이터에서 `runtime_profile!='standard'` 제외 (스펙 §11-1 확정)
- [x] `PUT/DELETE /api/agents/{id}` → 비표준 profile은 404 (enumeration-safe)
- [x] 히든 에이전트 lazy-seed + start v2 엔드포인트 (세션+워크스페이스+draft conversation 생성)
- 검증: `cd backend && uv run pytest -q -k "runtime_profile or skill_builder" && uv run ruff check .`
- done-when: seed/필터/404 가드/start v2 테스트 그린
- 상태: done (2026-07-07) — 대상 102 그린, 전체 2552 그린(SKILL_EVALUATION_ENABLED=true), ruff 클린
- §11-1 확정 표면(전수 grep): `agent_service.list_agents`/`list_agent_summaries`(+user_agent_ids 서브쿼리), `chat_service.list_global_conversations_page`(네비게이터 — 핵심 경로), `usage_service.get_usage_summary` by_agent, `usage_aggregate.get_daily_spend` agent/model축, `agent_api.list_deployment_candidates`, `read_tools.list_available_subagents`, `agent_service._validate_sub_agent_ids_owned`(서브에이전트 연결 차단). 사용자 총합(DailySpendUser)은 히든 비용 포함(실비용). GET 단건은 빌더 챗 서피스용으로 개방.
- 노트: 빌더 conversation은 source="draft" — 메시지 0개로 draft GC 리텐션 경과 시 삭제될 수 있음(session.conversation_id SET NULL). M6 프론트에서 null 처리 or 재생성 검토(Phase 1.5 후보).

## M2: 드래프트 워크스페이스 + 권한
- [x] `app/services/skill_draft_workspace.py`: 생성/시드(improve 복사)/첨부→`inputs/` 복사/dir→SkillDraftFile 어댑터/GC(세션 상태 기준)
- [x] `filesystem_permissions.py`: 세션 드래프트 allow → `/skill-drafts/**` deny → **`/uploads` deny(기존 구멍 수리, 별도 커밋 1abdbe5a)**
- [x] scheduler GC 잡 등록 (leader-only, `skill_draft_gc_retention_hours` 설정)
- 검증: `cd backend && uv run pytest -q -k "draft_workspace or filesystem_permissions"`
- done-when: 워크스페이스/권한(sibling deny 포함)/GC 테스트 그린
- 상태: done (2026-07-08) — 대상 121 그린, 전체 2566 그린, ruff 클린
- 노트: start v2가 improve 시드까지 배선(원본→워크스페이스 복사, symlink 금지). 어댑터는 `inputs/` 제외·바이너리 skip·역할 매핑 정본을 `skill_draft_workspace.role_for_path`로 이동(스냅샷 로더 위임). GC는 세션 상태 기준(active/confirming 무기한 보존) + orphan 디렉토리 mtime 폴백. `draft_workspace_path` 파라미터의 런타임 배선(AgentConfig 스레딩)은 M3.

## M3: 런타임 분기 + validate/generate_evals + 이벤트
- [x] `_prepare_runtime_components` 분기: prompt.md 교체, 도구 세트 교체, 드래프트 마운트, System LLM 재해석(`resolve_system_model('text_primary')`)
- [x] `validate_skill`/`generate_evals` 도구
- [x] `moldy.skill_draft`(stream-head stable-id) + `moldy.skill_validation`(tool projection) — event_names + `_redact_custom_event` 등록 필수
- 검증: `cd backend && uv run pytest -q -k "skill_builder or skill_draft"` + 수동: 실 대화에서 SKILL.md 점진 편집(edit_file) 확인
- done-when: 도구/이벤트/redaction 테스트 그린, 멀티턴 점진 편집 육안 확인
- 상태: done (2026-07-08) — 대상 113 그린, 전체 2578 그린. **수동 멀티턴 편집 육안 확인은 미수행(로컬 서버 필요)** — M6 E2E scripted 시퀀스로 대체 검증 예정, 실 LLM 육안 확인은 사용자 확인 필요.
- 구현 노트: System LLM 재해석은 `resolve_agent_context`의 `_resolve_skill_builder_agent_context` 분기에서(prepare는 cfg 값 사용). 세션은 conversation_id 역참조 + user 필터(enumeration-safe 404). 첨부→inputs 복사는 run.start 커맨드(`conversation_agent_protocol_commands`)에서 링크 직후 트리거. skill_validation projection은 `skill_validation_projection.py`(memory_event_projection 패턴) — finalize_skill(M5)도 같은 매처로 잡힘. redaction은 요약-전용 페이로드 계약의 명시 pass-through 등록.

## M4: test_skill_draft + HITL 세션 동의
- [ ] `test_skill_draft`: fabricated descriptor(DB row 불요) → 기존 샌드박스 정책 전체 상속
- [ ] 백엔드: `input.respond`의 `scope:"session"` → 동의 기록 + 표준 approve 변환 (비표준 type 미들웨어 도달 금지)
- [ ] 정책: 동의 시 policy 제외, `requires_network` 드래프트는 동의 불가
- [ ] 프론트: approval-card "이 세션에서 계속 허용" 옵션(review_configs 플래그 조건부)
- 검증: backend pytest + `cd frontend && pnpm vitest run` (transport mock 일괄 갱신 확인)
- done-when: 동의 플로우 테스트 그린 (1회차 카드→동의→2회차 무카드)
- 상태: pending

## M5: finalize_skill + 감사
- [ ] finalize: 검증 재실행→secret scan→claim→zip(synthetic Skill)→create/replace+리비전. 생성/개선/`SOURCE_SKILL_CHANGED`/slug 충돌 전 케이스
- [ ] 감사 이벤트(confirm_create/apply_improvement/skill_revision.create/secret_scan_blocked/apply_conflict) + 완료 딥링크 페이로드
- 검증: `cd backend && uv run pytest -q -k "finalize or skill_builder_confirm"`
- done-when: finalize 전 케이스 + 감사 테스트 그린
- 상태: pending

## M6: 프론트 라우트/레일 + 구경로 제거 + E2E
- [ ] `/skills/builder/[sessionId]` 라우트 (ChatRuntimeSection 마운트) + 진입점 교체(create 탭/improve 버튼)
- [ ] 검증 레일(훅+아톰+기존 패널 재사용) + i18n
- [ ] 구경로 제거: SkillBuilderDialog/stream-skill-builder-message/workflow 가짜 SSE/one-pass graph/가짜 평가
- [ ] E2E `E2E_SKILL_BUILDER` 마커 + 리로드 replay + 캡처 투어(스펙 §2 증빙)
- 검증: `cd frontend && pnpm build && pnpm vitest run` + E2E throwaway 스택(fresh ports) + `cd backend && uv run pytest`
- done-when: 스펙 §2 성공 기준 6건 전부 충족, 전체 스위트 그린
- 상태: pending

## 마일스톤 의존
M1 → M2 → M3 → {M4, M5 병렬 가능} → M6
