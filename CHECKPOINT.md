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
- [x] `test_skill_draft`: fabricated descriptor(DB row 불요) → 기존 샌드박스 정책 전체 상속
- [x] 백엔드: `input.respond`의 `scope:"session"` → 동의 기록 + 표준 approve 변환 (비표준 type 미들웨어 도달 금지)
- [x] 정책: 동의 시 policy 제외, `requires_network` 드래프트는 동의 불가
- [x] 프론트: approval-card "이 세션에서 계속 허용" 옵션(review_configs 플래그 조건부)
- 검증: backend pytest + `cd frontend && pnpm vitest run` (transport mock 일괄 갱신 확인)
- done-when: 동의 플로우 테스트 그린 (1회차 카드→동의→2회차 무카드)
- 상태: done (2026-07-08) — backend 2589 그린, vitest 1234 그린, tsc/eslint 클린
- 구현 노트: fabricated descriptor는 slug를 프론트매터에서 엄격 새니타이즈(traversal 방어), `agent_runtime_name=None`으로 non-agent 런타임 루트 강제(run_eval_skill_command slug 경로 일치), thread_id=`skill-draft-<sid>`(mtime 기반 runtime-root GC가 자동 청소). audit_kind `skill_builder.draft_test` 추가. 동의는 `conversation_agent_protocol_consent.py`가 resume 핸들러에서 **resolve_agent_context 이전**에 기록(AD-4 타이밍 — 동의 직후 resume부터 즉시 효력). requires_network는 기록/적용 양쪽 재검증. `session_consent_eligible` 플래그는 langchain ReviewConfig가 여분 키를 안 만들므로 wire 계층(`_annotate_session_consent_eligibility`)에서 주입 — **리로드(state 하이드레이션) 인터럽트에는 플래그 없음**(라이브 전용, v1 한계; 승인 자체는 가능). AD-3 과승인 방지로 빌더 분기의 write_file/edit_file 승인 카드 제외(M3 커밋의 기본 정책 잔재 수리).

## M5: finalize_skill + 감사
- [x] finalize: 검증 재실행→secret scan→claim→zip→create/replace+리비전. 생성/개선/`SOURCE_SKILL_CHANGED`/slug 충돌 전 케이스
- [x] 감사 이벤트(confirm_create/apply_improvement/skill_revision.create/secret_scan_blocked/apply_conflict) + 완료 딥링크 페이로드
- 검증: `cd backend && uv run pytest -q -k "finalize or skill_builder_confirm"`
- done-when: finalize 전 케이스 + 감사 테스트 그린
- 상태: done (2026-07-08) — 대상 27 그린, 전체 2597 그린, ruff 클린
- 구현 노트: 스펙의 synthetic-Skill zip 대신 **v1 confirm 플로우 최대 재사용** — 워크스페이스→SkillDraftPackage(`build_draft_package`)→`save_draft_package`(REVIEW)→`claim_for_confirming`→`confirm_builder_session`(검증 재실행+secret scan+create/improve+리비전+eval 수거 전부 상속). 대신 어댑터가 text-only라 **바이너리 패키지 파일은 fail-closed**(`BINARY_FILES_UNSUPPORTED` — improve 시드 원본의 asset 조용한 누락 방지, Phase 1.5에서 디스크 기반 zip으로 해제). finalize_skill 도구는 WRITE_INTERNAL/approve·reject/trigger_safe=False — 항상 승인 카드, SESSION_CONSENT_ELIGIBLE_TOOLS 비포함. 멱등(completed 세션 재호출 시 기존 skill 반환). 감사는 도구 경로용 서비스(`skill_builder_finalize`)에서 v1 어휘 그대로 기록(request=None).

## M6: 프론트 라우트/레일 + 구경로 제거 + E2E
- [x] `/skills/builder/[sessionId]` 라우트 (ChatRuntimeSection 마운트) + 진입점 교체(create 탭/improve 버튼)
- [x] 검증 레일(훅+아톰+기존 패널 재사용) + i18n
- [x] 구경로 제거: SkillBuilderDialog/stream-skill-builder-message/workflow 가짜 SSE/one-pass graph/가짜 평가
- [x] E2E `E2E_SKILL_BUILDER_*` 마커 + 리로드 replay + 캡처 투어(스펙 §2 증빙)
- 검증: `cd frontend && pnpm build && pnpm vitest run` + E2E throwaway 스택(fresh ports) + `cd backend && uv run pytest`
- done-when: 스펙 §2 성공 기준 6건 전부 충족, 전체 스위트 그린
- 상태: done (2026-07-08) — backend 2592 / vitest 1229 / tsc·eslint·pnpm build 그린, E2E `skill-builder-chat.spec.ts` 그린(21s) + 캡처 7장(`frontend/output/captures/skill-builder-chat/`, gitignore)
- 구현 노트(M6-3):
  - 마커는 5개로 분해: `E2E_SKILL_BUILDER_{WRITE,VALIDATE,TEST,RETEST,FINALIZE}` — WRITE 메시지에 워크스페이스 가상 경로를 실어 보낸다(scripted model은 세션 id를 모름). RETEST는 args가 달라야 승인 카드 pill-strip 키와 충돌하지 않는다(HITL_MULTI distinct-output 선례).
  - **E2E system LLM**: `seed_e2e_scripted_model`이 text_primary 미설정 시 scripted 모델로 시드(기설정은 불변). `.env`의 `E2E_LLM_*`이 있으면 실 LiteLLM이 이기므로 **E2E 실행 시 `E2E_LLM_BASE_URL='' E2E_LLM_API_KEY='' E2E_LLM_MODEL=''`로 비워야** 결정론.
  - E2E 함정 2건: ① 런 직후 Enter 전송이 무시될 수 있어 sendMessage 헬퍼(빈 값 확인+전송 버튼 폴백), ② 승인 resume 일시 실패 시 카드 재시도(hitl-approval.spec 계약). ③ finalize 카드는 직전 resolved 카드와 연속 request_approval 그룹("승인 대기 N건")으로 렌더될 수 있어 헤더 대신 finalize_skill 텍스트로 대기.
  - E2E 재실행법: `docker run -d --name moldy-sbchat-pg -p 5436:5432 ... postgres:16-alpine` → alembic head → `E2E_FRONTEND_PORT=3310 E2E_BACKEND_PORT=8310 DATABASE_URL(_SYNC)=...5436... RATE_LIMIT_ENABLED=false E2E_TEST_HELPERS_ENABLED=true E2E_LLM_*='' pnpm exec playwright test e2e/skill-builder-chat.spec.ts`
- 남은 백로그(Phase 1.5+): improve 충돌 후 re-seed, 바이너리 패키지 finalize(디스크 zip), 리로드 인터럽트의 세션 동의 플래그(라이브 전용), draft GC로 conversation 소실 시 재생성, create 탭 최초 요청의 자동 첫 메시지화.

## M7: 빌더 뷰 디자인 정합 (목업 차용)
- [x] 목업 실효성 감사 (`~/Downloads/Web-Prototype_skill/skill-studio.html` builder view 대조)
- [x] 백엔드: `GET /{sid}/files`(어댑터 기반 목록) + `GET /{sid}/files/content?path=`(정확 일치 — traversal=404) + brief에 `credential_requirement_count`
- [x] 프론트: 상태 카드 레일(검증 행+런타임 칩+메타 행), 소스 뷰어(레일 모드 전환), 헤더 헬퍼, try-hint(컴포저 프리필)
- [x] E2E/캡처 확장: 기능 spec에 상태카드·소스뷰어 단언, 캡처 03b(try-hint)/05b(소스 뷰어) 추가 → 17장
- 검증: backend 2597 / vitest 1237 / tsc·eslint·pnpm build 그린, 디자인 가드 12=베이스 동일(신규 위반 0), E2E 기능+캡처 투어 그린(throwaway 5436/3310/8310)
- done-when: 목업 차용 요소가 실데이터로 렌더 + 캡처 육안 대조 통과
- 상태: done (2026-07-08)
- **목업 감사 결정 (차용/각색/기각)**:
  - 차용: 검증 상태 카드(행별 통과/주의/오류 — 실제 검증기 이슈 코드 매핑: SKILL_MD_*/INVALID_PATH→frontmatter, MOLDY_METADATA/CREDENTIAL_REQUIREMENT→메타 분리, WEAK_TRIGGER/SCAFFOLDING→트리거(통과 시 'good' 톤), SECRET_DETECTED→시크릿), 런타임 호환 칩(compatibility.py TARGETS 3종 실데이터), 헤더 헬퍼 문구, composer 위 try-hint(dashed pill — 클릭 시 시험 요청 프리필), Credential/샌드박스/평가 메타 행
  - 각색: 소스 "탭"(목업 5탭 IA는 Phase 2) → **레일 모드 전환**(상태 보기 ↔ 소스 보기, 저장 전 드래프트라 새 파일 API 필요), 목업 "5/5 통과" 고정 카운트 → pending/pass/warn/error 헤드 필, mint #009966 → Moldy 토큰(--primary/--status-*)
  - 기각: 가짜 평가 숫자(86%→89%), composer 하드코딩 모델명/게이지/비용(이미 실데이터 존재), 자유 텍스트 안 chip-row(ask_user 소유), 목업 사이드냅(지식/데이터소스/테스트/배포 — Phase 1 범위 밖)
- 구현 노트: 파일 API는 디스크 트래버설 표면 없이 어댑터 경로 목록과 **정확 일치**만 허용(`skill_file_not_found()` 재사용, inputs/·바이너리 제외 어댑터 계약 그대로). 레일 파일 목록은 라이브 brief 우선 + 파일 API 폴백(진입 직후/improve 시드의 빈 레일 해소 — 캡처 13에서 육안 검증). improve 부제는 base_skill_version 없으면 버전 표기 생략(railSubtitleImproveNoVersion — finalize 스킬은 frontmatter version 없으면 None). 파생 로직은 `skill-builder-rail-model.ts` 순수 함수로 분리(+단위 테스트 16). 스트림 종료 시 files 쿼리 invalidate.

## M8: 채팅 결함 근본 수정 + 실 LLM 검증
- [x] M8-1 런 직후 Enter 드롭: `postRunHydrationPending`(런 종료 후 서버 상태 재조정)이 컴포저 런타임 `isRunning` 게이트에 OR — 하이드레이션 창에서 Enter가 조용히 드롭(전송 버튼은 Playwright auto-wait로만 통과해 보였음). 게이트에서 제외 — 메시지 연속성은 별도 `streamStateIsSettling`(sticky 레이어)이 유지. E2E sendMessage 폴백 제거(Enter 단독이 회귀 가드)
- [x] M8-2 승인 resume 일시 실패: 인터럽트 SSE는 스트림 중 즉시 flush되지만 run "interrupted" 커밋은 `finalize_trace` 뒤 — 그 창에서 승인하면 RESUME_NOT_FOUND. ① 인터럽트 런은 상태 전이를 trace 영속화보다 먼저 커밋(양방향 순서 회귀 테스트), ② resume 핸들러가 활성 run의 전이를 최대 2s 대기(run이 아예 없으면 즉시 실패 — fast-fail 테스트). E2E approveWithRetry 제거 + hitl-approval.spec 재시도 허용 제거(재시도 문구=회귀)
- [x] M8-3 "승인 대기 N건" 허수: 그룹 키가 도구명뿐이라 다른 인터럽트의 resolved+pending request_approval이 coalesce — 키에 `hitl_interrupt_id` suffix(`group-tool:request_approval:<id>`), 렌더는 `groupToolName`으로 도구명 복원. 캡처 09에서 단독 카드 확인 + E2E no-group 단언
- [x] M8-4 실 LLM 검증: LiteLLM(text_primary, `seed_e2e_llm`이 scripted 뒤에 덮음) 실구동 — 요청→초안(4파일: SKILL.md/추출규칙 ref/openai.yaml/evals 3케이스)→검증→인라인 시험(승인 카드→날짜 해석 포함 표 출력)→finalize(승인→저장·배너·딥링크) **전 플로우 통과(2.6분)**. 프롬프트 품질 양호 — 튜닝 불필요 판정
- [x] M8-4 수확 — **실 LLM 전용 크래시 3건 수정**: 스트리밍 부분 JSON args 무가드 배열 연산 (`write_todos` todos.filter → 채팅 전체 에러 바운더리 다운, `ask_user` questions/options .map, clarifying 카드 parsed.options). scripted 모델은 완성 args만 방출해 기존 E2E 전부 그린인 채 놓침. 방어 정규화 + red→green 회귀 4건(`partial-streaming-args.test.tsx`)
- 검증: backend 2601(ruff 클린) / vitest 1245 / tsc·eslint·build 그린. E2E 기능 3/3(--retries=0) + hitl-approval + 캡처 투어 그린. red→green 검증: M8-1 유닛, M8-2 워커 순서·bounded wait, M8-4 가드
- 상태: done (2026-07-08) — 커밋 764180bc(M8-1~3) + 후속 커밋(M8-4 가드)
- **백로그(라이브 resume 뷰 전용 표시 결함 — 영속/리로드는 깨끗함을 DB·리로드 프로브로 확정)**:
  - 실 LLM finalize 턴에서 사용자 버블 중복 렌더(라이브만, run/이벤트/리로드엔 1개)
  - 실 LLM finalize 후 raw `finalize_skill` pill이 빨간 ✗로 잔존(성공인데 모순 표시; 리로드는 초록 ✓ — stripInterruptedRawToolCalls의 라이브 real-LLM resume 경로 미스)
  - 승인 배지 위치: resolved synthetic 메시지가 대화 말미에 append되어 원 위치가 아닌 곳에 적층(scripted 캡처 10에서도 동일 — 기존 동작)
  - E2E 인프라: dev 서버 콜드 런에서 컴포저 remount 순간 press가 끼면 Enter 1회 유실 가능(격리 3/3 통과 — 인프라 flake 분류, prod 빌드 무관)

## 마일스톤 의존
M1 → M2 → M3 → {M4, M5 병렬 가능} → M6 → M7 → M8
