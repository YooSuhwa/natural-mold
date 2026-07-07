# SPEC — 스킬 스튜디오 Phase 1: 스킬 빌더 챗 (메인챗 이관)

| 항목 | 내용 |
|------|------|
| 상태 | Draft v1 — 리뷰 대기 |
| 작성일 | 2026-07-07 |
| 브랜치 | `feature/skill-builder-chat` |
| 선행 결정 | 옵션 B(진짜 멀티턴 tool-using 에이전트) 확정. 제품 결정 5건 확정(§1.3) |
| 관련 | ADR-013(System LLM credential), ADR-018(상대 storage path), ADR-019(System LLM Settings), M59(conversation_artifacts), M64(skill_builder_sessions), `docs/design-docs/chat-feature-gap-analysis.md`(G1) |
| 목업 | `~/Downloads/Web-Prototype_skill/skill-studio.html` — 빌더 뷰("메인 채팅 그대로 가져온 빌더 대화" + 검증 사이드레일)가 Phase 1의 타깃 |

---

## 1. 배경과 목표

### 1.1 현재 상태 (문제)

현행 "스킬 빌더 대화"는 대화가 아니다:

- 드래프트는 구조화 JSON **1회 호출로 패키지 전체를 매번 재생성** (`JsonChatDraftWorker`, `app/agent_runtime/skill_builder/graph.py:46-68`). 이전 메시지를 LLM에 넣지 않는다(입력은 `{intent, mode, base_snapshot}`뿐, `graph.py:210-215`).
- 프로덕션 경로에 checkpointer 없음 — 상태는 `skill_builder_sessions`의 JSON 컬럼.
- SSE는 12종 이벤트를 **가짜 스크립트로 방출**(`services/skill_builder_workflow.py:96-143`)하고, 프론트는 `error` 외 전부 무시(`skill-builder-stream-events.ts`). `content_delta`도 no-op — 어시스턴트 텍스트를 렌더하지 않는다.
- 프론트는 요청 textarea + Start 버튼의 2-pane 다이얼로그(`skill-builder-dialog.tsx`). 말풍선/멀티턴/첨부/HITL 없음.
- 빌더 세션 평가는 **가짜 결과**를 만든다(`skill_builder_eval_service.py:44-60` — all-pass/all-fail 조작).

### 1.2 목표

스킬 생성·개선을 **메인챗과 동일한 진짜 멀티턴 대화**로 바꾼다. 에이전트가 드래프트 파일을 **점진적으로 실제 편집**하고, 사용자는 대화 도중 **자기 예시로 즉시 시험**하며, 확정은 **HITL 승인 카드**로 이뤄진다. 우측 **검증 레일**이 드래프트 상태·검증 결과를 실시간 반영한다.

### 1.3 확정된 제품 결정 (2026-07-06)

1. **인라인 "예시로 시험" = 핵심 기능.** 드래프트 워크스페이스 + 스코프드 동의(HITL)로 정면 구현. 우회 금지.
2. **"저장 안 됨" 개념 제거.** working tree(드래프트 워크스페이스, 연속 영속) / commit(리비전, 명시적 확정) 모델.
3. **벤치마크는 진짜 A/B로** (Phase 3 — 본 스펙 범위 밖, §11).
4. **비용·연결 카운트 실데이터화** (Phase 2/3 — 범위 밖, §11).
5. **진짜 라우트 기반 풀페이지 스튜디오** (Phase 2 — 본 스펙은 빌더 라우트만 신설).

### 1.4 프로그램 로드맵

| Phase | 내용 | 스펙 |
|-------|------|------|
| **1 (본 스펙)** | 스킬 빌더 → 메인챗 런타임 이관: 히든 빌더 에이전트, 드래프트 워크스페이스, 도구 5종, HITL 세션 동의, 검증 레일, `/skills/builder/[sessionId]` 라우트 | 이 문서 |
| 2 | 스튜디오 IA(5탭 라우트), 목록 표+벌크, 버전 SKILL.md diff, 연결 에이전트 실카운트, credentials/metadata 탭 재배치 | 별도 |
| 3 | 평가 진실성: 실제 with/without A/B, 비용 실회계, 케이스 휴먼 피드백, 버전별 통과율 추이 | 별도 |

---

## 2. 성공 기준 (검증 가능)

1. 사용자가 `/skills` → "대화로 만들기" → 채팅에서 여러 턴에 걸쳐 스킬을 설명·수정하면, 에이전트가 `write_file`/`edit_file`로 `SKILL.md`·`references/`·`evals/evals.json`을 **점진 편집**한다 (매 턴 전체 재생성 아님 — 체크포인트에 대화 이력 유지).
2. 사용자가 "이 예시로 시험해줘"(첨부 포함 가능) 하면 `test_skill_draft`가 **저장 전 드래프트**를 샌드박스에서 실행하고 결과를 채팅에 보여준다. 첫 실행은 승인 카드, "이 세션에서 계속 허용" 선택 시 이후 실행은 카드 없이 진행된다.
3. `finalize_skill`은 **항상** 승인 카드를 띄우고, 승인 시 진짜 `skills` row + `skill_revisions` 리비전이 생성된다(생성/개선 모두). 개선 모드에서 원본이 변경됐으면 `SOURCE_SKILL_CHANGED`로 실패하고 에이전트가 사용자에게 설명한다.
4. 우측 검증 레일이 드래프트 파일 목록·검증 상태(오류/경고)·호환성을 표시하고, **리로드 후에도** 이벤트 replay로 복원된다.
5. 브라우저를 닫았다 다시 열어도 세션·대화·드래프트가 그대로 재개된다 (드래프트 = 디스크, 대화 = checkpointer, 세션 = DB).
6. 검증: backend pytest 전체 그린, vitest 전체 그린, tsc/eslint 그린, 신규 E2E(scripted model) 그린, 캡처 투어로 각 성공 기준 시각 증빙.

---

## 3. 아키텍처 결정

### AD-1. 실행 표면 = 실제 v3 메인챗 런타임 + per-user 히든 빌더 에이전트 row

**결정**: 사용자마다 `runtime_profile='skill_builder'`인 실제 `agents` row를 **lazy-seed**(첫 빌더 진입 시 생성)하고, 빌더 대화는 그 에이전트의 **진짜 conversation**으로 v3 agent-protocol 위에서 돈다.

**근거** (소스 검증 완료):
- `conversations`에는 `user_id`가 없다 — 소유권은 전적으로 `Agent.user_id` 조인으로 강제된다(`chat_service.py:967-984`, `get_owned_thread` `conversation_agent_protocol_runtime.py:31-44`). 사용자 소유 row면 **v3 스택 전체(커맨드/스트림/HITL/체크포인트 fork/runs/재접속/message_events 영속/아티팩트/usage)가 무변경으로 동작**한다.
- `user_id IS NULL` 시스템 에이전트는 소유권 조인 전면 재작성이 필요해 기각. builder_v3식 별도 와이어는 message_events 영속·runs·재접속·아티팩트 레일을 포기하게 되어 기각(`use-chat-runtime.ts:1084-1091` — 재접속은 conversations 라우터 전용).
- 정밀 선례: assistant 패널이 "코드 정의 프롬프트 + 코드 빌드 도구 + build_agent"를 이미 증명(`assistant/assistant_agent.py:55-99`) — 단 그쪽은 레거시 와이어라서, 본 스펙은 같은 조립 방식을 **v3 경로 안에서** 한다.

**필요한 변경**:
- `agents.runtime_profile` 컬럼 추가(migration, `varchar`, default `'standard'`, 값: `standard|skill_builder`).
- 목록 오염 방지: `agent_service.list_agents`(`agent_service.py:68`)/`list_agent_summaries`(`:87,:137`)에서 `runtime_profile='standard'` 필터. 대시보드/일일 대화 집계 등 **크로스-에이전트 집계에서도 제외**(구현 시 사용처 grep 필수).
- 변조 방지: `PUT/DELETE /api/agents/{id}`(`routers/agents.py:191,260`)는 `runtime_profile!='standard'`면 404(enumeration-safe 규칙 준수 — 403 아님).
- 모델: 히든 row의 `model_id`는 seed 시점 값이되, **런타임에는 항상 `resolve_system_model(db,'text_primary')`로 재해석**(ADR-019; 현행 빌더와 동일 — `skill_builder/agent.py:18-32`). 미설정 시 기존 `SYSTEM_LLM_NOT_CONFIGURED` 계약 유지.

### AD-2. 드래프트 워크스페이스 = `data/skill-drafts/<session_id>/` 쓰기 가능 가상 마운트

**결정**: 세션마다 물리 디렉토리 `data/skill-drafts/<session_id>/`를 만들고, 가상 경로 `/skill-drafts/<session_id>/`로 에이전트에 노출한다. 에이전트는 deepagents 표준 `write_file`/`edit_file`/`read_file`/`ls`로 직접 편집한다.

**근거**: FilesystemBackend는 단일 루트(`backend/data`) + virtual_mode라 `data/` 하위는 자동으로 주소 가능(`runtime_component_builder.py:657`, deepagents `backends/filesystem.py:176-217`). 쓰기 가능 여부는 순수하게 권한 규칙 문제 — `build_filesystem_permissions`(`filesystem_permissions.py:21-82`)의 `/**` write-deny(`:79`) **앞에** 해당 세션 서브트리 allow read+write 규칙을 삽입하면 끝.

**규칙** (순서 중요, first-match-wins):
1. allow read+write `/skill-drafts/<이 세션 id>/**`
2. deny read+write `/skill-drafts/**` (타 세션 차단 — unmatched 기본이 allow이므로 필수)
3. 기존 규칙 유지. **추가 보안 수리**: 같은 이유로 현재 `/uploads`가 기본-allow로 노출되어 있음(교차 사용자 노출) — deny 규칙을 이번에 함께 추가(§6).

**동작 특성** (구현 시 프롬프트에 반영):
- deepagents `write()`는 기존 파일 덮어쓰기를 거부(`filesystem.py:486-488`) → 수정은 `edit_file`. 빌더 프롬프트에 명시.
- 개선(improve) 모드: 세션 시작 시 원본 스킬 파일을 워크스페이스로 **복사**(스킬 마운트의 copytree 선례 — `skill_runtime.py:181-213`, symlink 금지) + `base_content_hash` 기록(기존 충돌 모델 계승).
- 첨부: run 시작 시 대화에 연결된 업로드(`message_attachments.storage_path`, 파일은 `data/uploads/<uuid><ext>` — `uploads.py:135`)를 `<workspace>/inputs/<원본파일명>`으로 **복사**. G1 갭(첨부가 모델에 전달 안 됨)의 빌더 스코프 해결 — uploads 전체 마운트는 금지(§6).
- GC: `cleanup_stale_runtime_roots` 스켈레톤 복제(`skill_runtime.py:381-418` + `scheduler.py:679-725` 등록 패턴)하되 **mtime이 아니라 세션 상태 기준**(active/confirming 세션은 보존, `completed`/`abandoned`만 리텐션 경과 시 삭제). 설정: `skill_draft_gc_retention_hours`(pydantic-settings, `config.py:75` 스타일).

### AD-3. 도구 세트 (코드 빌드, 5종 + FS 기본)

`_prepare_runtime_components`(`runtime_component_builder.py:571-749`)에 `cfg.runtime_profile == 'skill_builder'` 분기를 추가한다. 분기에서: 코드 정의 시스템 프롬프트(`prompt.md` 로드 패턴 — `assistant_agent.py:31-44`), `tools_config` 루프·`execute_in_skill`·memory 도구·subagents **생략**, 아래 도구를 append. `ask_user`(`:727-728`)와 temporal 도구는 유지(명료화 질문에 필요).

| 도구 | 동작 | 재사용 (전부 기존 함수) | HITL |
|------|------|------|------|
| (FS 기본) `ls/read_file/write_file/edit_file` | 드래프트 편집 | deepagents FilesystemMiddleware — 추가 작업 없음, 권한이 스코프 | write/edit: deepagents 기본 위험 메타 유지 판단은 구현 시(과승인 방지 위해 워크스페이스 내부는 interrupt 제외 권장 — fs 권한 `interrupt` 모드 대신 allow) |
| `validate_skill` | 드래프트 dir → `SkillDraftFile` 리스트 어댑터 → 검증+호환성. 결과를 모델과 검증 레일(§AD-5) 양쪽에 | `validate_draft_package`(`skills/validator.py:29-78`), `check_portable_compatibility`(`compatibility.py:30-110`). 입력 계약: `Sequence[SkillDraftFile]`(text-only — 바이너리는 skip, `errors="replace"` 선례 `skill_builder_service.py:162-191`) | 없음(읽기 전용) |
| `test_skill_draft` | 드래프트를 샌드박스 실행. fabricated descriptor `{id: session uuid, slug, storage_path: 'skill-drafts/<sid>'}` → `build_skill_runtime_context(output_root=data/skill-draft-runs)` → `run_eval_skill_command` | descriptor는 **dict-driven, DB row 불요**(`skill_runtime.py:216-248`; credential 해석은 missing row를 조용히 skip `:344-346`). 전체 subprocess 정책 자동 적용: allowlist/timeout(기본 30s, cap 420s)/`requires_network` curl 게이트/SSRF 정책/credential env/`redact_credential_values`/output 수집(`skill_executor.py:37-217`, `skill_execution_policy.py`) | CODE_EXECUTION(approve/reject) + **세션 동의**(AD-4) |
| `generate_evals` | 평가 케이스 생성 → `evals/evals.json`을 드래프트에 기록 | `select_eval_template`+`generate_eval_cases`(`eval_case_generator.py:7-74`), 스키마 가드 `parse_evals_json`+limits(`eval_limits.py:5-8`). finalize가 이 파일을 자동 수거(`skill_builder_evaluations.py:129-147`) | 없음 |
| `finalize_skill` | 확정: 검증 재실행 → `claim_for_confirming` → 드래프트 zip → skills row + 리비전 | zip은 `build_installed_skill_zip_bytes`에 **미영속 synthetic Skill**(`kind='package'`, `storage_path='skill-drafts/<sid>'`)을 넘김(바이너리 safe, DB 불요 — `package_exporter.py:12-34`). 생성: `create_package_skill`(전체 zip 방어 재통과, `service.py:136-188`) + `unique_skill_slug`. 개선: `lock_skill_for_mutation` → `base_content_hash` 검사 → `SOURCE_SKILL_CHANGED` → `replace_skill_storage`(`skill_builder_package_storage.py:22-42`). 리비전 `builder_create/builder_improvement`(`skill_revision_service.py:31-74`). secret scan(`marketplace.secret_scan`)은 finalize 전 필수 게이트 | **항상 승인 카드** (세션 동의 불가) |

도구의 DB 접근: request-scoped 세션을 장수명 스트림에 고정하지 않도록 **세션 팩토리 클로저**로 전달(선례: memory 도구가 런 중 DB 쓰기 수행 — `runtime_component_builder.py:629-639`; assistant 도구 클로저 패턴 `assistant_agent.py:84-87`).

개선 모드 충돌(v1 정책): `finalize_skill`이 `SOURCE_SKILL_CHANGED` 도구 에러를 반환 → 에이전트가 설명하고 "최신 기준으로 새 세션 시작"을 안내. 워크스페이스 re-seed 도구는 Phase 1.5.

### AD-4. HITL 스코프드 동의 ("이 세션에서 계속 허용")

**결정**: `test_skill_draft`에 `attach_tool_risk`(CODE_EXECUTION, `risk.py:134-150` 패턴)를 달아 기본은 매 호출 승인 카드. 승인 카드에 **"이 세션에서 계속 허용"** 옵션을 추가하고, 선택 시:

1. 프론트가 `input.respond` decisions에 확장 필드(예: `scope: "session"`)를 실어 보낸다.
2. 백엔드 커맨드 핸들러(`conversation_agent_protocol_commands.py:216-317`)가 이를 **세션 row의 동의 상태로 기록**하고 미들웨어에는 **표준 `approve`만 전달**한다 — 비표준 decision type은 langchain 미들웨어가 ValueError(`human_in_the_loop.py:343-349`). 절대 그대로 내려보내지 않는다.
3. `resolve_agent_context`가 동의 상태를 `AgentConfig`로 스레딩 → `_build_interrupt_on_policy`(`runtime_component_builder.py:363-392`)가 `test_skill_draft`를 정책에서 제외.
4. **타이밍 보장**: 에이전트는 **모든 resume에서 재빌드**된다(`langgraph_agent_stream_runner.py:89`) — 동의는 인터럽트 응답 시점에만 발생하므로, 동의 직후의 resume부터 즉시 효력. 런 중간 동적 변경 불필요.

**경계**: 드래프트 `execution_profile.requires_network == true`면 세션 동의 **불가**(매번 카드). 조건부 정책이 필요하면 미들웨어가 네이티브 지원하는 `when` predicate(`human_in_the_loop.py:194-213`) 사용. `finalize_skill`은 항상 카드.

프론트: `review_configs`에 세션 동의 가능 플래그를 실어 approval-card가 옵션을 조건부 렌더(기존 `allowed_decisions` 게이팅 패턴 — `approval-card.tsx:477-486` 연장).

### AD-5. 검증 레일 = 기존 커스텀 사이드채널 이벤트 패턴

**결정**: 두 종류 이벤트로 레일을 구동한다 (둘 다 기존 계약 복제):

1. **`moldy.skill_draft`** — stream-head 1회, stable id `f"{run_id}:skill_draft"` (선례: `_memory_recalled_event`, `langgraph_streaming.py:214-266`). 페이로드: 세션 id/모드/slug/파일 목록 요약/base 대비 변경 수. prepare 시 `AgentConfig` 필드에 적재(`runtime_config.py:59-65` 패턴).
2. **`moldy.skill_validation`** — `validate_skill`/`finalize_skill` 도구 결과 projection (선례: `UI_DATA_TOOL_TRANSFORMERS`, `ui_data_projection.py:63-65`; 수집 `protocol_side_effects.py:223-265`). 페이로드: 기존 `validation_result`/`compatibility_result` 스키마 그대로 (프론트 패널 재사용 목적).

**필수 등록** (CLAUDE.md 규칙): `event_names.py:33-58`에 이름 추가 + `protocol_redaction.py`의 커스텀 이벤트 matcher에 등록(비밀 미포함 페이로드라도 등록 누락은 회귀 위험). wire/persist 이중 redaction은 `emit()` 경유로 자동(`langgraph_streaming.py:338-345`, `protocol_persistence.py:15-24`).

프론트: `useChannelEffect(stream, ['custom'], {replay:true})` + Jotai atom + 대화 스코프 dedup(선례 그대로: `subagent-names-events.ts:76-106`, `data-ui-events.ts:196-251` — dedup은 entity id 기준, 대화 전환 시 seen 리셋). 레일 패널은 기존 컴포넌트 재사용: `skill-builder-preview*.tsx`의 ValidationPanel/PortableCompatibilityPanel/파일 요약(페이로드 shape 불변이므로 대부분 생존).

### AD-6. 세션·대화 매핑과 진입 플로우

- `skill_builder_sessions` v2: `conversation_id`(FK conversations, nullable, index — `m64:54-58` 인덱스 패턴), `draft_workspace_path`(ADR-018 **상대경로**, `ensure_relative` 필수 — `storage/paths.py:44-54`). `messages`/`draft_package` JSON은 소스오브트루스에서 해제(파생/레거시). 상태 기계 정리: `active → confirming → completed` + `abandoned`(GC 대상) — 현행 `drafting/failed/cancelled`는 미사용 확인됨(dead state).
- 시작 플로우: `POST /api/skill-builder`(v2) = 히든 에이전트 lazy-seed → 세션 row + 워크스페이스 생성(+improve면 원본 복사) → draft conversation 생성(`conversation_crud.py:206-232` 재사용) → `{session_id, agent_id, conversation_id}` 반환 → 프론트 `/skills/builder/[sessionId]` 이동.
- 죽는 것: `POST /{id}/messages`·`/messages/resume`(가짜 SSE, `routers/skill_builder.py:97-142`)와 `skill_builder_workflow.py` 전체, one-pass 그래프(`graph.py` 5노드·두 워커), `SkillBuilderState`, 가짜 평가(`skill_builder_eval_service.py:44-60`), 프론트 `skill-builder-dialog.tsx`+`stream-skill-builder-message.ts`. `GET /{id}`·`/validate`·`/confirm`은 적응 유지(confirm은 도구 경로가 주가 되고 REST는 세션 상태 조회/관리용).

---

## 4. 백엔드 상세 설계

### 4.1 마이그레이션 (번호는 구현 시점 head 기준, M67~)

1. `agents.runtime_profile varchar NOT NULL DEFAULT 'standard'` + index 불요(목록 필터는 user_id 선행).
2. `skill_builder_sessions`: `conversation_id Uuid NULL FK conversations.id ON DELETE SET NULL` + index, `draft_workspace_path varchar(500) NULL`, (선택) `tool_consents JSON NULL`(AD-4 동의 저장).

### 4.2 신규/변경 모듈

| 모듈 | 책임 |
|------|------|
| `app/agent_runtime/skill_builder/prompt.md` (신규) | 빌더 시스템 프롬프트: 목적 수집 → 점진 편집(`edit_file` 사용 규칙) → 검증 → 시험 → finalize 제안. 포터블 스킬 원칙(기존 prompt.md 계승: <500줄 SKILL.md, 트리거는 frontmatter description, references/ 분리, secrets 금지) |
| `app/services/skill_draft_workspace.py` (신규) | 워크스페이스 생성/시드(improve 복사)/첨부 복사(`inputs/`)/디렉토리→`SkillDraftFile` 어댑터/GC. 경로는 전부 `ensure_relative`/`resolve_data_path` |
| `app/agent_runtime/skill_builder/tools.py` (신규) | AD-3 도구 5종(클로저 + 세션 팩토리). `attach_tool_risk` 부착 |
| `runtime_component_builder.py` (분기) | `cfg.runtime_profile=='skill_builder'` 분기: 프롬프트 교체, 도구 세트 교체, 드래프트 마운트 권한, `moldy.skill_draft` 페이로드 적재 |
| `conversation_stream_service.py` (확장) | `resolve_agent_context`: runtime_profile 판독, System LLM 재해석, 세션 조회(conversation_id 역참조), 동의 상태 스레딩, 첨부→`inputs/` 복사 트리거 |
| `filesystem_permissions.py` (확장) | 드래프트 마운트 allow + sibling deny + `/uploads` deny |
| `conversation_agent_protocol_commands.py` (확장) | `input.respond`의 `scope:"session"` 동의 기록(미들웨어엔 표준 approve만) |
| `ui_data_projection.py`/`event_names.py`/`protocol_redaction.py` (확장) | `moldy.skill_draft`/`moldy.skill_validation` 등록 |
| `routers/skill_builder.py` (개편) | start v2 / get / abandon. messages·resume 삭제 |
| `app/scheduler.py` (확장) | drafts GC 잡 등록(leader-only, `replace_existing` — `:699-725` 패턴) |

### 4.3 감사 이벤트 (기존 어휘 계승)

`skill_builder.session_create`(`routers/skill_builder.py:77` 계승), `skill_builder.draft_test`(신규 — test_skill_draft 실행, sandbox denial은 기존 `skill_executor` 감사 자동), `skill_builder.confirm_create`/`apply_improvement`(`:244,:261-264`) + `skill_revision.create`(`skill_builder_audit.py:66-83`), `skill_builder.secret_scan_blocked`(`skill_builder_support.py:118`), `skill_builder.apply_conflict`(`:216`).

---

## 5. 프론트 상세 설계

### 5.1 라우트/진입

- 신규 `/skills/builder/[sessionId]/page.tsx`: 세션 조회 → `ChatRuntimeSection`(`chat-runtime-section.tsx:116-134`)을 `{agentId(히든), conversationId}`로 마운트. 메인챗 서피스(AssistantThread v3) 그대로 — 컴포저/첨부/HITL 카드/토큰 게이지/재접속 전부 상속.
- 진입점 교체: `SkillCreateDialog` chat 탭(`skill-create-tabs.tsx:14`)과 상세 다이얼로그 "improve by chat"(`skill-detail-dialog.tsx:122`)이 start v2 호출 후 신규 라우트로 이동. `SkillBuilderDialog`는 삭제.
- finalize 완료 시: 완료 카드에서 `/skills?detailId=<skill_id>` 딥링크(Phase 2에서 스튜디오 라우트로 승격).

### 5.2 검증 레일

- 신규 `skill-builder-rail.tsx`: `moldy.skill_draft`/`moldy.skill_validation` 훅+아톰 구동. 패널 재사용: ValidationPanel·PortableCompatibilityPanel(`skill-builder-preview*.tsx` 생존분), 드래프트 파일 트리(read-only), 개선 모드 변경 요약(`fileDiffSummary` 로직 계승 — `skill-builder-preview-model.ts:98-134`).
- 레일 배치는 기존 아티팩트 우측 레일 패턴. 모바일 접힘.

### 5.3 승인 카드 확장

- `review_configs`의 세션 동의 플래그 → approval-card에 "이 세션에서 계속 허용" 선택지(체크박스/보조 버튼). 선택 시 decisions에 `scope:"session"` 첨부. `allowed_decisions` 게이팅(`approval-card.tsx:477-486`)과 동일한 조건부 렌더 원칙.
- 주의(기존 규칙): 승인 카드가 대표하는 raw tool call pill 중복은 `stripInterruptedRawToolCalls` 계약 유지 — `test_skill_draft`/`finalize_skill`도 매칭 대상에 포함되는지 확인.

### 5.4 i18n

- 신규 네임스페이스 `skillBuilderChat`(레일/카드/진입). 기존 `skill.builderDialog` 키 중 검증/호환성/충돌 키는 페이로드 shape 불변이므로 이관 재사용. **주의**: navigator/루트 레이아웃에서 열리는 다이얼로그는 chat 스코프 밖(export 다이얼로그 선례) — 레일은 챗 페이지 내부라 chat 스코프로 충분.

---

## 6. 보안

1. **권한 규칙 순서**: 세션 드래프트 allow → `/skill-drafts/**` deny → 기존 규칙 → `/**` write-deny. first-match-wins(`deepagents middleware/filesystem.py:126-136`)라 순서가 곧 보안.
2. **`/uploads` 기본-allow 구멍 봉쇄**: 현재 unmatched 기본 allow로 임의 에이전트가 `ls("/uploads")` 가능(교차 사용자 노출, 리서치 검증). 본 작업에서 deny 추가 — 빌더와 무관한 **기존 취약 수리**이므로 별도 커밋.
3. **첨부는 복사, 마운트 금지**: 대화에 연결된 첨부만 `<workspace>/inputs/`로 복사. uploads 전체 read 마운트는 스코프 위반.
4. **finalize 전 secret scan 필수**(`marketplace.secret_scan` — 업로드 경로 선례 `skill_uploads.py:57-62`). 차단 시 `secret_scan_blocked` 감사.
5. **zip 방어 재통과**: finalize는 `create_package_skill` 경유라 `extract_package`의 symlink/zip-slip/null-byte/size(50MB)/count(1000) 방어를 다시 통과(`packager.py:67-80`, `config.py:106-107`).
6. **subprocess 정책 무변경 상속**: `test_skill_draft`는 `execute_in_skill`과 동일 컨텍스트 정책(allowlist/timeout/SSRF/redaction) — 새 실행 표면을 만들지 않는다.
7. **이벤트 redaction 등록**: 신규 moldy.* 이벤트를 `_redact_custom_event` matcher에 등록(CLAUDE.md 규칙). 드래프트 파일 내용은 이벤트에 싣지 않는다(요약·카운트만) — 파일 내용은 도구 결과/FS 읽기로만.
8. **enumeration-safe**: 세션/에이전트 조회 실패는 전부 404 통일.

---

## 7. 테스트 계획

### 백엔드 (pytest, aiosqlite)
- 워크스페이스: 생성/시드(improve 복사)/첨부 복사/어댑터(바이너리 skip)/GC(상태 기준 — active 보존).
- 권한: 드래프트 마운트 allow/sibling deny/`/uploads` deny (permission 룰 단위 테스트).
- 도구: validate(이슈 shape)/test(fabricated descriptor 실행 + 정책 게이트 + slug 불일치 거부)/generate_evals(파일 기록+스키마 가드)/finalize create·improve(+`SOURCE_SKILL_CHANGED`+secret scan 차단+slug 충돌).
- HITL: `scope:"session"` 기록 + 표준 approve 변환(비표준 type이 미들웨어에 도달하지 않음을 단언), requires_network 드래프트의 동의 불가.
- 이벤트: `moldy.skill_draft` stable-id, `moldy.skill_validation` projection, persist redaction 통과.
- runtime_profile: 목록 필터/PUT·DELETE 404/System LLM 재해석/미설정 에러.

### 프론트 (vitest)
- 레일 훅/아톰(replay dedup, 대화 스코프 리셋), 패널 재사용 렌더, 승인 카드 세션 동의 옵션(플래그 조건부), 진입 플로우.
- **공유 transport mock 규칙 준수**: `MoldyAgentServerAdapter` 인터페이스 변경 시 `createMockTransport()` 일괄 갱신 + `pnpm vitest run` 전체.

### E2E (scripted model + throwaway 스택)
- 신규 마커 `E2E_SKILL_BUILDER`: scripted 시퀀스 = write_file(SKILL.md) → validate_skill → test_skill_draft(승인 카드→세션 동의→2회차 무카드) → finalize_skill(승인 카드→승인) → skills row 단언. (`e2e_scripted_model.py` 확장 — wave2 마커 선례.)
- 리로드 replay: 레일 복원 + `<redacted>` 부재.
- 캡처 투어: 진입/멀티턴 편집/시험 카드/세션 동의/레일/finalize/완료 딥링크 (§2 증빙).
- 주의(기존 학습): 첫 spec self-warm, `settle()` networkidle 5s cap, ask_user resume 금지, fresh ports.

---

## 8. 구현 마일스톤 (CHECKPOINT.md로 전개)

| M | 내용 | done-when |
|---|------|-----------|
| M1 | 마이그레이션 2종 + runtime_profile 필터/가드 + 히든 에이전트 lazy-seed + start v2 | pytest: seed/필터/404 가드 그린 |
| M2 | 워크스페이스 서비스 + 권한 규칙(+`/uploads` 수리) + GC 잡 | pytest: 워크스페이스/권한/GC 그린 |
| M3 | 런타임 분기(프롬프트/도구 골격) + validate_skill + generate_evals + 이벤트 2종 | pytest: 도구/이벤트 그린, 수동 대화로 SKILL.md 점진 편집 확인 |
| M4 | test_skill_draft + HITL 세션 동의(백+프론트 카드) | pytest+vitest 그린, E2E 동의 플로우 |
| M5 | finalize_skill(생성/개선+충돌) + 감사 + 완료 딥링크 | pytest: finalize 전 케이스 그린 |
| M6 | 프론트 라우트/레일/진입 교체 + 구경로 제거 + i18n + E2E/캡처 전체 | §2 성공 기준 전부, 전체 스위트 그린 |

각 마일스톤 완료 시 커밋. `SKILL_EVALUATION_ENABLED=true`로 push 검증(기존 학습).

---

## 9. 리스크와 완화

| 리스크 | 완화 |
|--------|------|
| 히든 에이전트가 목록/집계/네비게이터에 누출 | M1에서 사용처 전수 grep + 필터, E2E로 목록 부재 단언 |
| deepagents write 거부(덮어쓰기)로 에이전트 루프 | 프롬프트에 edit_file 규칙 명시 + 도구 에러 메시지가 이미 자체 안내("Read and then make an edit") |
| 드래프트 이벤트 페이로드 비대화(파일 내용 포함 유혹) | 요약만 싣는 계약을 스키마로 고정(pydantic+Zod allowlist — ui_data fail-safe 선례) |
| 세션 동의가 의도보다 넓게 적용 | requires_network 예외 + finalize 항상 카드 + 동의는 도구 단위·세션 단위로만 |
| improve 충돌 UX가 대화에서 어색 | v1은 명시적 에러+안내, re-seed 도구는 Phase 1.5 백로그 |
| 체크포인트 비대(긴 빌더 세션) | 기존 compaction 계약이 v3에 이미 존재(moldy.compaction) — 상속 |

---

## 10. Phase 1 범위 밖 (명시)

- 스튜디오 5탭 IA/목록 표/벌크/버전 diff/연결 카운트 (Phase 2)
- 실제 with/without A/B 벤치마크·비용 실회계·휴먼 피드백·버전별 통과율 (Phase 3)
- 워크스페이스 re-seed(개선 충돌 후 이어가기), 텍스트 스킬 전용 경량 플로우, LLM 기반 eval 케이스 질 개선, 트리거 적합성 도구화 (Phase 1.5 백로그)
- G1 멀티모달 일반 해결(본 스펙은 빌더 스코프 복사로만 해결)

## 11. 오픈 퀘스천 (구현 전 확정 필요)

1. 히든 에이전트의 대화가 **대시보드 최근 대화/일일 집계**에 노출되는 표면의 정확한 목록 — M1 grep에서 확정.
2. 빌더 대화의 공유(share link) 허용 여부 — v1 기본: 허용하되 특별 취급 없음(redaction 계약이 이미 적용). 이견 시 차단.
3. `test_skill_draft`의 출력 파일(`data/skill-draft-runs/`)을 대화 아티팩트 레일에 노출할지 — v1: 도구 결과 텍스트로만(터미널 ui_data projection 재사용 검토).
