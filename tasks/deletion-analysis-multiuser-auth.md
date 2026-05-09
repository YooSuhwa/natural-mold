# Deletion Analysis — Multi-User Auth (Musk Step 2)

> Goal: 멀티유저 전환 전, 제거/단순화 가능한 모든 코드를 식별. 분석/문서만, 코드 수정 없음.
>
> 분석 대상 브랜치: `feature/multiuser-auth` · 참조 plan: `~/.claude/plans/replicated-crunching-lark.md`
>
> ⚠️ `tasks/deletion-analysis.md`는 다른 세션 산출물 — 손대지 않음.

---

## 1. Mock User 흔적 전수조사

검색 명령:
```bash
grep -rn "mock_user" backend/app/
grep -rn "00000000-0000-0000-0000-000000000001" backend/
grep -rni "mock\|MOCK_USER\|DEMO_USER" backend/.env.example backend/app/
```

| # | 위치 | 코드 요약 | 처리 방안 |
|---|------|-----------|-----------|
| 1 | `backend/app/config.py:31-34` | `mock_user_id`, `mock_user_email`, `mock_user_name` 3개 Settings 필드 | **제거** — 멀티유저 전환과 동시에 의미 없음. JWT 설정으로 대체. |
| 2 | `backend/app/dependencies.py:11-29` | `CurrentUser` dataclass + `get_current_user()`가 settings에서 mock 반환 | **재작성** — `CurrentUser`에 `is_super_user: bool=False` 필드 추가. `get_current_user`는 cookie/Authorization → JWT decode → DB lookup으로 전면 교체. `get_current_user_optional`, `require_super_user` 신규 추가 (plan 2.4). |
| 3 | `backend/app/main.py:96-141` (lifespan seed 블록) | `mock_user_id = uuid.UUID(settings.mock_user_id)` → User row upsert → `bootstrap_credentials_from_env(db, mock_user_id)` | **제거** — Mock user upsert 블록(96-108) 삭제. `bootstrap_credentials_from_env(db, mock_user_id)` 호출은 **`bootstrap_system_credentials(db)` (`is_system=True`, `user_id=NULL`)**로 변경 (plan 4.1/4.2). 다른 시드(default models, templates, env_fallback sync)는 글로벌이라 보존. |
| 4 | `backend/.env.example` | (검증 결과) **이미 mock 섹션 없음** — `MOCK_USER_*` 환경변수 노출 안 됨 (config.py 기본값으로만 존재) | **변경 없음** (env.example 측). 단, plan 10.1 신규 키들(`JWT_SECRET`, `COOKIE_*`, `ALLOW_FIRST_USER_AS_ADMIN`) 추가는 별개 작업. |
| 5 | `backend/app/seed/bootstrap_from_env.py:1-159` | 모듈 docstring부터 mock_user 전제. 시그니처 `bootstrap_credentials_from_env(db, user_id: uuid.UUID)` 사용. 라인 105-156이 user-bound credential 생성. | **재작성** — 함수명 `bootstrap_system_credentials(db)`로 변경, `user_id` 파라미터 제거. `credential_service.create(db, user_id=None, is_system=True, ...)`로 변경. 기존 `Credential.user_id NOT NULL` 제약(model line 26-28, m18 line 146-148)을 nullable로 마이그레이션 필요. docstring/SEED_NAME_PREFIX(`"[env]"`)는 유지 가능. |
| 6 | `backend/app/routers/credentials.py:248` | docstring `"All operator system credentials. PoC: no role gate (mock user)."` | **재작성** — docstring 갱신 + 라우터에 `Depends(require_super_user)` 추가 (`list_system_credentials`, `create_system_credential`, `get_system_credential`, `update_system_credential`, `delete_system_credential` 5개 모두). |
| 7 | `backend/tests/conftest.py:36` | `TEST_USER_ID = uuid.UUID("00000000-...0001")` | **보존(이유)** — 테스트 픽스처. Mock user UUID와 우연히 같지만 의미는 다름(`TEST_USER_ID`). 멀티유저 테스트 신설 시 `TEST_USER_A_ID`/`TEST_USER_B_ID`로 확장. 기존 단일 유저 테스트는 그대로. |
| 8 | `backend/tests/test_credentials_llm_sync.py:430` | `assert TEST_USER_ID == uuid.UUID("00000000-...0001")` | **보존(이유)** — 위 #7과 같은 테스트 상수. 변경 불필요. |
| 9 | `backend/tests/test_seed.py:1` | docstring `"bootstrap_credentials_from_env — env → mock_user Credential seed."` | **재작성** — `bootstrap_system_credentials`로 함수가 바뀌면 테스트 자체도 `is_system=True` 검증으로 교체. |

**제거 후 LOC 추정**: config.py -4, dependencies.py +25/-7, main.py -10, bootstrap_from_env.py +5/-15, credentials.py +5 (super_user 가드). 순 **약 -30 LOC + 의미 단순화**.

---

## 2. FK ON DELETE 정책 매트릭스

검색 명령:
```bash
grep -rn "user_id" backend/app/models/ | grep -v "__pycache__"
grep -n "ondelete" backend/alembic/versions/aa5b4cc59ddb_initial_tables.py backend/alembic/versions/m18_greenfield_credentials.py
```

| 테이블 | 컬럼 | 모델 위치 | 현재 ondelete | 변경 후 | 마이그레이션 필요 |
|--------|------|-----------|---------------|---------|-------------------|
| `agents` | `user_id` | `agent.py:23` | **미명시** (initial m1, line 96-99) | **CASCADE** | 예 — drop+recreate FK |
| `builder_sessions` | `user_id` | `builder_session.py:19` | **미명시** | **CASCADE** | 예 — drop+recreate FK (m35는 `agent_id`만 처리) |
| `agent_triggers` | `user_id` | `agent_trigger.py:19` | **미명시** | **CASCADE** | 예 — drop+recreate FK |
| `agent_creation_sessions` | `user_id` | (legacy?) | **미명시** (initial line 70-73) | **CASCADE** 또는 테이블 자체 검토 — `builder_sessions`로 대체된 듯 | 예 (legacy 잔존 테이블이면 drop도 검토) |
| `tools` | `user_id` | `tool.py:51-52` | **CASCADE** (m18 line 245-248, nullable) | 유지 — `is_system` 컬럼 추가 시 정합성 검토 | 아니오 |
| `credentials` | `user_id` | `credential.py:26-28` | **CASCADE** (m18 line 146-148, NOT NULL) | **NULL 허용 + CASCADE** — `is_system=True` row가 user_id NULL이어야 함 | 예 — nullable 변경 + (선택) CHECK constraint `(is_system=true AND user_id IS NULL) OR (is_system=false AND user_id IS NOT NULL)` |
| `credential_audit_logs` | `actor_user_id` | (m18 line 180-182) | **SET NULL** | 유지 | 아니오 |
| `daily_spend_users` | `user_id` | `daily_spend_user.py:34-35` | **CASCADE** | 유지 | 아니오 |
| `share_links` | `created_by` | `share_link.py:32` | **CASCADE** | 유지 | 아니오 |
| `mcp_servers` | `user_id` | `mcp_server.py:44-45` | **CASCADE** | 유지 — `is_system` 컬럼 이미 존재 (m26, model line 84) | 아니오 |
| `skills` | `user_id` | (m18 line 320-322) | **CASCADE** | 유지 | 아니오 |
| `message_feedback` | `user_id` | (별도 m27) | (확인 필요) | CASCADE 권장 | (확인) |
| `message_attachments` | `user_id` | (m28) | (확인 필요) | CASCADE 권장 | (확인) |
| **신규** `refresh_tokens` | `user_id` | (plan 1.3) | — | **CASCADE** (생성 시점부터) | 예 — m22 신설 |
| **Phase 2** `oauth_accounts` | `user_id` | — | — | CASCADE | (Phase 2) |

**핵심 마이그레이션 작업** (Alembic m22 또는 후속 m36):
1. `agents.user_id`, `builder_sessions.user_id`, `agent_triggers.user_id` → CASCADE
2. `credentials.user_id` → nullable로 변경 (CASCADE는 유지)
3. `refresh_tokens` 테이블 신설
4. (선택) `agent_creation_sessions` 테이블 미사용 여부 확인 후 drop

---

## 3. 라우터별 인가 audit

검색 명령:
```bash
grep -n "^@router\|user.id\|require_super\|get_current_user" backend/app/routers/*.py
```

| 라우터 | GET 엔드포인트 user 필터 | mutation user 필터 | 누락된 검증 | 우선순위 |
|--------|---------------------------|---------------------|-------------|----------|
| `agents.py` | OK (`agent_service.list_agents(db, user.id)`, `get_agent(db, id, user.id)`) | OK (전 엔드포인트가 `agent_service.get_agent(..., user.id)`로 owner 검증) | 없음 | — |
| `conversations.py` | OK (`get_owned_conversation` enumeration-oracle 안전) | OK | 없음 — chat_service join 패턴 우수 | — |
| `tools.py` | OK (`_load_owned`) | OK | 없음 | — |
| `credentials.py` (owner CRUD) | OK (`_load_owned` per row) | OK | 없음 — audit log 포함 | — |
| `credentials.py` (system CRUD `/api/system-credentials/*`) | **누락** — `list_system_credentials`(line 244)는 모든 인증 사용자에게 시스템 credential 노출, `_load_system`(line 232)이 super_user 가드 없음 | **누락** — `create/update/delete_system_credential`(255/294/320)이 일반 user에게 허용 | **`Depends(require_super_user)` 5개 엔드포인트 모두 추가**. 비용 폭주 + 키 누출 위험 | 🔴 **HIGH** |
| `builder.py` | OK (`builder_service.get_session(db, id, user.id)`) | OK | 없음 | — |
| `triggers.py` | OK (`trigger_service.get_trigger`) | OK | 없음 | — |
| `usage.py` | OK (`DailySpendUser.user_id` 직접 필터 + Agent join) | — (read-only) | 없음 | — |
| `feedback.py` | OK (`MessageFeedback.user_id == user.id`) | OK | 없음 | — |
| `mcp.py` | OK (`McpServer.user_id == user.id`) | OK | `is_system` MCP server fallback 정책 검토 필요 (super_user만 변경 가능?) | 🟡 MEDIUM |
| `skills.py` | OK (`skill_service.get_skill(db, id, user.id)`) | OK | 없음 | — |
| `uploads.py` | OK (`user_id=user.id`) | OK | 없음 | — |
| `assistant.py` | OK (`agent_service.get_agent(db, agent_id, user.id)`) | OK | 없음 | — |
| `health.py` | OK (`McpServer.user_id == user.id`) | OK | 없음 | — |
| `templates.py` | 글로벌 OK (read-only — line 16-29) | **N/A** — create/update/delete 엔드포인트 자체가 **존재하지 않음** | plan 5.1은 super_user-only POST를 추가할 것을 가정하지만 현재 라우터에는 없음. 신설 시점에 가드 추가하면 됨 | 🟢 LOW (작업 시 add) |
| `models.py` | 글로벌 OK (line 52-70) — 모든 사용자에게 동일 catalog | **누락** — `create_model`(72), `update_model`(121), `delete_model`(150)이 일반 user에게 허용. catalog는 글로벌 자원 | **`Depends(require_super_user)` 추가** (POST/PATCH/DELETE 3개) | 🔴 **HIGH** |
| `shares.py` | owner: `_require_owned_conversation`로 OK / public(`/api/shares/{token}`): 인증 없음 OK | `create_share`(78)/`revoke_share`(93) — owner 검증 OK | 없음 — 잘 구현됨 | — |

**즉시 조치 후보 (HIGH)**:
1. `credentials.py` 시스템 credential 라우터 5개 → `require_super_user` 추가
2. `models.py` 카탈로그 mutation 3개 → `require_super_user` 추가
3. **모든 mutation에 `Depends(verify_csrf)` 일괄 적용** (라우터 prefix 또는 미들웨어)

---

## 4. 시드 데이터 user 종속성

검색 명령:
```bash
ls backend/app/seed/
grep -n "user_id\|mock_user_id" backend/app/seed/*.py
```

| 시드 위치 | user 종속성 | 변경 필요 |
|-----------|-------------|-----------|
| `backend/app/seed/default_models.py` (DEFAULT_MODELS) | **없음** — 글로벌 catalog (`models` 테이블, user_id 없음) | 변경 없음 |
| `backend/app/seed/default_templates.py` (DEFAULT_TEMPLATES) | **없음** — 글로벌 (`templates` 테이블, user_id 없음) | 변경 없음 |
| `backend/app/seed/bootstrap_from_env.py` | **mock_user_id 종속**: 시그니처 `bootstrap_credentials_from_env(db, user_id)` 라인 105-149. 호출자(`main.py:134`)가 mock_user_id 전달. 함수 내부에서 user 존재 검증 후 user-owned credential 생성 | **변경 필요** — `bootstrap_system_credentials(db)`로 시그니처 변경, `is_system=True, user_id=None`. user 검증 블록 제거. plan 4.2/4.3에 정확히 매핑됨 |
| `backend/app/main.py:131-141` (lifespan에서 호출) | mock_user_id 전달 | `bootstrap_system_credentials(db)`로 호출 변경 |
| `backend/app/main.py:146-155` (`sync_env_fallback_from_credentials`) | user 비종속 | 변경 없음 |
| 시스템 도구(`is_system=True` Tool rows) | 자동 시드 — `tool_factory` 빌트인 레지스트리에서 `user_id IS NULL`로 처리 | 변경 없음 (이미 글로벌) |

**핵심**: 모든 시드는 **이미 글로벌**이거나 **bootstrap_from_env 한 곳만 user 종속**. 그 한 곳을 system credential로 바꾸면 시드 영역의 mock-user 종속성은 0이 된다.

---

## 5. 격리 위반 가능성 (보안 분석)

### 5.1 LangGraph Checkpoint thread_id 격리

- `backend/app/agent_runtime/checkpointer.py:51-58` — `delete_thread(thread_id)` 함수 존재.
- `executor.py:579` — `config = {"configurable": {"thread_id": cfg.thread_id}}` (= `conversation_id` 그대로).
- **위험**: `thread_id`가 **UUID v4**라 brute-force는 사실상 불가능하나, 라우터 외에서 thread를 직접 호출하는 경로 (e.g. trigger 실행 — `trigger_executor.py`)에서 user owner 검증을 빠뜨리면 cross-user 누출 가능.
- **현재 안전장치**: 라우터의 `chat_service.get_owned_conversation` join이 강력하다. 단, **user 삭제 시 LangGraph checkpoint cascade가 없음** — orphan thread 누적 우려 (plan 6.1에서 `cleanup_user_resources` 신설로 해결).

### 5.2 Tool factory cross-user credential leak

- `tool_factory.py:217` — `user_uuid = _safe_uuid(tool_config.get("user_id"))` (caller가 주입). chat_service `build_tools_config`에서 `user_id=str(agent.user_id)` 전달(`chat_service.py:586`).
- **누출 경로**: 만약 어떤 caller가 `user_id`를 주입하지 않으면 `_build_tool_hook_context`가 `None` 반환 → spend tracking 누락 + audit hole. **누출은 아니지만 격리 시그널 손실**.
- **잠재적 위반**: `system_credential_resolver.py`(`is_system=True` credential lookup)가 일반 user의 도구 호출에서도 동작 가능. plan 4.2가 정의한 "super_user 전용" 정책이 코드에는 아직 박혀 있지 않음 → **별도 가드 함수 신설 필요** (`assert_can_use_system_credential(user)`).

### 5.3 Conversation join 패턴

- `chat_service.get_owned_conversation`(line 91-105) — `Conversation` ⨝ `Agent` on `Agent.user_id == user_id` 단일 SELECT. 최적화 우수.
- **검증 결과**: `Conversation` 테이블 자체에는 `user_id` 없음 (`conversation.py`). 격리는 **Agent 경유 1-hop**. Agent 삭제 시 Conversation도 cascade되어야 — 현재 `agents.id` FK에 ondelete CASCADE 명시 안 된 듯 (initial migration line 125-128). **확인 필요**.

### 5.4 Daily spend aggregation 격리

- `usage_aggregate.py:104-179` — user axis는 직접 column 필터, agent/model axis는 Agent.user_id join. 패턴 깨끗함. 격리 위반 없음.

### 5.5 Builder session FK 일관성

- `builder_session.py:19` — `user_id ForeignKey("users.id")` ondelete 미명시. `agent_id`는 m35로 SET NULL 처리됨. **user 탈퇴 시 builder_session row 고아 우려** → CASCADE로 통일.

---

## 6. 제거 후보 (Musk Step 2 — 명시적 단순화)

명시적으로 **삭제하면 단순해지는** 코드:

| # | 대상 | 위치 | LOC 추정 | 단순화 효과 |
|---|------|------|----------|-------------|
| 1 | `mock_user_id`/`mock_user_email`/`mock_user_name` Settings 필드 | `config.py:31-34` | -4 | "Mock user (PoC: no auth)" 개념 자체 제거 |
| 2 | `get_current_user` mock 반환부 | `dependencies.py:23-29` | -7 | JWT 기반으로 전면 교체. mock 분기 사라짐 |
| 3 | `main.py` lifespan의 mock user upsert 블록 | `main.py:96-108` | -13 | startup 코드 13줄 + import 1줄 (`User`) 제거 가능 (import는 다른 사용처 확인 후) |
| 4 | `bootstrap_credentials_from_env`의 user 검증 블록 | `bootstrap_from_env.py:120-126` | -7 | system credential 전환 후 불필요 |
| 5 | `bootstrap_credentials_from_env`의 `user_id` 파라미터 + `Credential.user_id == user_id` 필터 | `bootstrap_from_env.py:105-156` | -3 | 시그니처 단순화 |
| 6 | `routers/credentials.py:248` docstring `"PoC: no role gate (mock user)."` | line 248 | -1 | 의미 명확화 |
| 7 | `agent_creation_sessions` 테이블 (legacy, **dead**) | initial migration line 61-75 | -15 (마이그레이션 drop) | **확인 완료**: `grep -rn "agent_creation_sessions\|AgentCreationSession" backend/app/`가 0건. `builder_sessions`로 완전 대체됨. **drop 가능** — m22 또는 후속 마이그레이션에 `op.drop_table("agent_creation_sessions")` 추가. |
| 8 | `Settings.google_oauth_refresh_token`(`config.py:29`) 글로벌 사용처 | `agent_runtime/google_workspace_tools.py` (확인 필요) | -? | 글로벌 토큰 → 사용자별 credential로 전환 시 settings 필드 제거 가능 |

**총 LOC 감소 예상**: 단순 Mock 흔적 제거 = **약 35 LOC**. `agent_creation_sessions` legacy 테이블 drop 시 +50~100 LOC. 의미적 단순화 효과는 LOC보다 큼 — "Mock User"라는 단일 사용자 가정 자체가 사라지면 라우터 인가 audit, 시드 정책, 도구 credential 우선순위가 일관된 모델로 정리된다.

---

## 7. 검증 명령어 모음

다음 세션에서 이 분석을 재현/확장할 때 사용:

```bash
# 1. Mock user 흔적 — 모두 0건이 되어야 멀티유저 전환 완료
grep -rn "mock_user" backend/app/
grep -rn "00000000-0000-0000-0000-000000000001" backend/
grep -rni "mock\|MOCK_USER\|DEMO_USER" backend/.env.example backend/app/

# 2. FK ondelete 정책 매트릭스
grep -rn "user_id" backend/app/models/ | grep -v "__pycache__"
grep -n "ondelete" backend/alembic/versions/aa5b4cc59ddb_initial_tables.py
grep -n "ondelete" backend/alembic/versions/m18_greenfield_credentials.py
grep -n "ForeignKey.*users" backend/app/models/*.py

# 3. 라우터 인가 audit
grep -n "^@router\|user.id\|require_super\|get_current_user" backend/app/routers/*.py
grep -rn "require_super\|is_super_user" backend/app/  # 신설 후 검증

# 4. 시드 데이터 user 종속성
grep -n "user_id\|mock_user_id" backend/app/seed/*.py
grep -rn "bootstrap_credentials_from_env\|bootstrap_system_credentials" backend/

# 5. 격리 위반 가능성
grep -n "thread_id\|user_id" backend/app/agent_runtime/checkpointer.py
grep -n "user_id\|owner\|is_system\|require_super" backend/app/agent_runtime/tool_factory.py
grep -n "get_owned_conversation\|Agent.user_id" backend/app/services/chat_service.py
grep -n "user_id\|is_system" backend/app/services/system_credential_resolver.py

# 6. 제거 후보 — legacy table 사용처 확인
grep -rn "agent_creation_sessions\|AgentCreationSession" backend/app/
grep -rn "google_oauth_refresh_token\|GOOGLE_OAUTH_REFRESH_TOKEN" backend/

# 7. is_super_user / 인증 신규 인프라 (도입 후 검증용)
grep -rn "is_super_user\|hashed_password\|RefreshToken" backend/app/
grep -rn "verify_csrf\|require_super_user\|get_current_user_optional" backend/app/

# 8. CSRF 누락 mutation 식별 (도입 후)
grep -n "@router\.\(post\|put\|patch\|delete\)" backend/app/routers/*.py | grep -v "auth\|verify_csrf"
```

---

## 부록 A — 우선순위 요약 (젠슨이 가장 먼저 처리할 것)

1. 🔴 **시스템 credential super_user 가드** (`routers/credentials.py:243-336`) — 비용 폭주/키 누출 위험. plan 4.2/5.1.
2. 🔴 **모델 카탈로그 mutation super_user 가드** (`routers/models.py:72/121/150`) — 글로벌 자원 보호.
3. 🔴 **`Credential.user_id`를 nullable로 마이그레이션** — `is_system=True` row를 user 비종속으로 만들기 위한 전제. 이거 없이는 plan 4.1의 `bootstrap_system_credentials` 동작 불가.
4. 🟡 **`agents/builder_sessions/agent_triggers` user_id FK ondelete=CASCADE** — user 탈퇴 시 고아 row 정리.
5. 🟡 **`get_current_user` 전면 재작성** + `dependencies.py`에 `require_super_user`/`get_current_user_optional`/`verify_csrf` 추가.
6. 🟢 **`main.py` mock user 블록 제거 + `bootstrap_credentials_from_env` 재작성** — 위 1~5가 끝나야 안전하게 가능.
