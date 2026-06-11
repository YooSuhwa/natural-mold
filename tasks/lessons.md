# Lessons — Cumulative Patterns (across sessions)

## Session 9 (2026-05-31) — ask_user QuestionFlow / OptionList

### Keep HiTL tool identity stable; branch on payload mode
**상황**: `ask_user`에 단계형 질문과 옵션 리스트 UI를 추가하면서 별도
`question_flow`/`option_list` 도구를 만들 수 있었음.

**패턴**:
1. Runtime tool name과 interrupt policy는 `ask_user` 하나로 유지한다.
2. `mode`/`questions`/`minSelections`/`maxSelections` 같은 UI shape는 payload args로 둔다.
3. Streaming bridge는 native interrupt dict에서 `type`만 빼고 나머지를 그대로
   frontend에 전달한다.
4. Frontend는 `respond(message)` contract를 유지하되, structured 답변은 JSON string으로
   직렬화하고 사람이 보는 receipt는 label text로 별도 보관한다.

**효과**: 기존 persisted tool call, approval policy, Builder pending-card 패턴을 깨지
않고 새 UI를 추가할 수 있다. 중복 도구를 만들면 정책/registry/test surface가 동시에
늘어나므로 피한다.

### Browser harnesses belong outside the product tree and should be short-lived
**상황**: 로컬 DB/LLM 상태에 의존하지 않고 React Tool UI를 브라우저에서 눌러보려고
`/private/tmp`에 임시 Vite harness를 만들었음.

**규칙**: 임시 하네스는 프로젝트 설정으로 착각될 수 있으므로, 목적을 명확히 설명하고
검증 직후 삭제한다. 실제 제품 설정 파일은 기능 구현 이유가 없으면 건드리지 않는다.

### Pending tool call + standard interrupt can double-render the same card
**상황**: Builder가 pending `ask_user` AI tool call을 먼저 emit하고, 같은 pause에서
standard interrupt event도 emit했다. Frontend가 interrupt를 synthetic tool call로
항상 추가하면 같은 ask_user 카드가 두 번 보인다.

**패턴**: interrupt synthetic tool call을 추가하기 전에 같은 이름과 같은 args의 기존
`ask_user` tool call이 있는지 확인한다. 있으면 새 카드를 push하지 말고 기존 args에
`hitl_*` metadata만 병합한다.

## Session 6 (2026-04-28) — Agent Edit Workbench

### Hybrid controlled/uncontrolled component pattern
**상황**: 단일 컴포넌트(`VisualSettingsFlow`)가 두 컨텍스트에서 다르게 동작해야 함 — 별도 라우트(internal state, 자체 Save) vs 워크벤치 inline(상위 page state, 단일 Save).

**패턴**:
1. 옵션 props 신설: `embedded?: boolean`, `controlledState?: {...}`, `controlledHandlers?: {...}`
2. 명시적 가드: `const isControlled = embedded && !!controlledState && !!controlledHandlers`
3. 모든 read/write/useEffect/callback에 `isControlled` 분기. `isControlled`이면 props 사용, 아니면 internal state setter
4. 자체 UI(Save 버튼 등)는 `{!embedded && <Toolbar />}`로 conditional
5. 분기 누락 방지: 검증 시 `isControlled` 사용 위치 grep — 모든 callback에 등장하는지 확인

**적용 시 주의**: agent sync useEffect는 controlled 모드에서 early return해야 internal state가 props를 덮어쓰지 않음.

### Pydantic v2 — 공유 validator를 두 스키마(Create + Update)에서 재사용
**상황**: `AgentCreate`와 `AgentUpdate`에 동일한 `opener_questions` 검증 로직이 필요.

**패턴**:
```python
def _validate_opener_questions(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if len(value) > 12:
        raise ValueError("최대 12개")
    cleaned = [s.strip() for s in value]
    if any(not s for s in cleaned):
        raise ValueError("빈 항목 불가")
    if any(len(s) > 200 for s in cleaned):
        raise ValueError("항목 200자 초과")
    return cleaned

class AgentCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opener_questions: list[str] | None = None

    @field_validator('opener_questions')
    @classmethod
    def _v_opener(cls, v): return _validate_opener_questions(v)

class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opener_questions: list[str] | None = None

    @field_validator('opener_questions')
    @classmethod
    def _v_opener(cls, v): return _validate_opener_questions(v)
```

**Gotcha**: `extra='forbid'`이므로 신규 필드를 두 클래스 **모두**에 등록해야 함. 한쪽만 등록하면 422.

### `_to_response` 단일 통로 패턴
**상황**: 신규 응답 필드 추가 시 어디에 빠뜨리면 응답에 반영 안 됨.

**원칙**: ORM → Response 변환은 라우터의 `_agent_to_response()` 같은 단일 헬퍼 함수에만 두기. 신규 필드 추가 시 이 함수 한 곳만 수정.

### assistant-ui composer 텍스트 주입 (전송 X)
**상황**: 빈 화면의 예시 질문 버튼/제안 칩 클릭 시 입력창에 텍스트만 채우고 사용자가 전송하도록 하고 싶음.

**패턴**:
```tsx
const composer = useComposerRuntime()
const onClick = (text: string) => composer.setText(text)
```

**Gotcha**: `useComposerRuntime`은 `<AssistantRuntimeProvider>` 자식에서만 사용 가능. 빈 화면 컴포넌트가 provider 자식인지 확인. 아니면 컴포넌트 추출 필요(이번 세션의 `ChatEmptyState`).

### shadcn Tabs로 좌/우 분할 워크벤치
**상황**: 한 페이지에 두 개의 독립적인 Tabs 그룹.

**원칙**:
- 각 Tabs를 독립적 state로 관리(value/onValueChange)
- `<Tabs value={leftTab}>` + `<Tabs value={rightTab}>` 별개 인스턴스
- defaultValue 대신 controlled value (page state)

### Next.js 16 + `use(params)` 패턴 (그대로 유지)
- `params: Promise<{ agentId: string }>` 시그니처
- `const { agentId } = use(params)` 호출
- `use-client` 컴포넌트에서 `use` 호출 가능

---

## Session 5 (2026-04-28) — Chat UI 안정화 + 시간 시스템

### `useFormatter` (next-intl) 타입은 `Intl.DateTimeFormatOptions`와 비호환
- timeZoneName 등 일부 옵션이 next-intl 자체 타입으로 좁혀져 있음
- 유틸 함수에서 Formatter 타입 직접 정의 시 빌드 실패
- 해법: `type Formatter = ReturnType<typeof useFormatter>`로 그대로 import

### assistant-ui — `useAssistantState`로 message.createdAt 접근
- 패턴: `useAssistantState((s) => (s.message as { createdAt?: Date } | undefined)?.createdAt)`
- 타입 캐스팅이 필요한 이유: message union 타입 분기

### AssistantThread 회귀 방지
- builder v3 등 다른 페이지도 사용
- 신규 시각 변경(메시지 시간 라벨)은 반드시 `showMessageTimestamp?: boolean` 옵셔널 prop으로 게이팅
- 채팅 페이지에서만 `true` 전달

### shadcn `DropdownMenuTrigger.render` 패턴
- `render={<Button ... aria-label={...} />}`로 Button을 trigger로 위임
- aria-label은 render 안에 둬야 a11y 보장

### Anthropic streaming list-content
- multi-block content가 `list[dict]`로 올 때 `isinstance(delta, str)`만 처리하면 token streaming 0
- `content_to_text` 공유 헬퍼로 평탄화 필요

### refetch 깜박임 방지
- `setStreamingMessages([])`를 `finally`에서 즉시 호출하면 refetch 도착까지 답변이 사라짐
- 해법: `prevMessagesRef` rendering-time 비교로 messages 변경 후에만 clear

### 채팅 viewport `min-h-0`
- `ThreadPrimitive.Root`/`Viewport`에 `min-h-0` 없으면 메시지 많을 때 입력창이 화면 밖으로 밀림

---

## Session 8 (2026-05-09) — ADR-016 멀티유저 인증 통합 검증 (S7)

### 라우터 commit 누락이 실패 경로의 보안 부작용을 무력화한다
**상황**: `auth_service.authenticate`에서 비밀번호 오류 시 `record_login_failure(db, user)`로 카운터 증가 → 즉시 `AppError` raise. 라우터(`login_endpoint`)는 success path에서만 `await db.commit()`을 호출. raise 후 request scope가 종료되며 `async with async_session()` exit이 자동 rollback → 카운터 증가가 영구히 사라짐.

**영향**: failed_login_attempts가 항상 0이라 5회 lockout이 작동하지 않음. brute-force 무제한.

**동일 클래스의 두 번째 instance**: `rotate_refresh`의 replay 감지 시 `_revoke_all_active(db, user_id)` UPDATE 후 raise. 마찬가지로 mass-revoke가 롤백되어 ADR-016 §5.2의 핵심 방어가 무력화.

**패턴화된 fix**:
1. **Best**: 보안-부작용 함수(record_login_failure, _revoke_all_active)는 별도 short-lived session에서 commit. 라우터 transaction과 분리.
2. **Worse but minimal**: 라우터에서 `try: ...; except AppError: await db.commit(); raise`.

**검증 회로**: 단위 테스트에서 `failed_login_attempts == 1`을 단순 assert해도 잡힌다. 통합 테스트가 commit 경계를 노출했음.

### conftest의 `verify_csrf` override는 마이그레이션 후 legacy 테스트를 살리는 정석 패턴
**상황**: S3가 routers에 `verify_csrf` Depends를 추가. 기존 conftest의 `client` fixture는 cookie/header를 세팅하지 않으므로 모든 mutation 테스트가 일제히 403. 149개 테스트가 빨갛게 됨.

**해법**: 단순히 `app.dependency_overrides[verify_csrf] = _bypass_verify_csrf` 추가. legacy 테스트의 의도 보존(=mutation 자체의 validation/business logic 검증), 신규 테스트는 별도 `raw_client` fixture로 진짜 cookie+CSRF flow 검증.

**규칙**: 마이그레이션 시 보안 dependency를 추가하면 conftest에 명시적 bypass + 새 fixture를 동시에 도입할 것. 한쪽만 하면 회귀가 가려지거나 noise만 늘어남.

### Test fixture는 ORM identity-map과 transaction scope를 모두 분리해야 한다
**상황**: 라우터가 `async with async_session()`로 별도 세션을 만들어 commit/rollback. 테스트의 `db` fixture는 별개 세션. 테스트가 `db.execute(select(User))`로 조회해도 라우터가 막 commit한 변경이 identity-map staleness로 보이지 않음.

**해법**: `_fresh_user(email)` 헬퍼로 `async with TestSession() as fresh: ...` 새 세션을 열어 조회. 매 assertion에서 새 read 트랜잭션. SQLite in-memory에서 동일 engine을 공유하므로 commit 가시성은 보장됨.

### Enumeration oracle 차단을 테스트가 직접 검증해야 한다
**상황**: ADR/security.md 규칙에 "404 vs 403" 통일이 명시되어 있어도, 실제 service 구현이 분기 응답을 만들면 테스트로 잡지 않으면 회귀.

**패턴**: 격리 매트릭스 테스트(`test_multiuser_isolation.py`)는 모든 owner-scoped 자원에 대해 cross-user 접근 시 정확히 `404`를 assert. enumeration prevention은 status code 단위로 hard-assert해야 한다.

### m22 → m36 ADR 번호 보정 사례
**상황**: ADR-016 작성 당시 마이그레이션 번호 m22를 가정했으나, 실제 작성 시 m36까지 진행되어 있었음. 코드가 ADR을 추월하는 흔한 케이스.

**규칙**: ADR은 "어느 마이그레이션에 속하는지"보다 "어떤 결정을 내렸는지"에 집중. 마이그레이션 번호는 코드 작성 시점에만 확정. ADR을 amend할 때는 본문 갱신 + Status 섹션에 "originally drafted assuming m22; landed as m36" 같은 노트만.

### AgentConfig.user_id Optional + post_init 가드 — legacy callsite 호환의 모범
**상황**: 새 `user_id` 필드를 dataclass에 강제하면 모든 기존 호출부가 한 번에 깨진다. `Optional[uuid.UUID]`로 두고 `__post_init__`에서 `if self.user_id is None: raise ValueError`.

**효과**: 신규 callsite는 컴파일 시점이 아니라 instantiation 시점에 검증. 마이그레이션 안전망이 단계적으로 도입 가능.

**주의**: 영구 Optional로 남기지 말 것 — 모든 callsite 마이그레이션이 끝나면 dataclass field 자체를 required로 승격하고 `__post_init__` 가드를 제거하는 후속 PR이 필요.

### 첫 가입자 super_user 자동화의 운영 위험
**상황**: `allow_first_user_as_admin=True`가 기본. DB가 비면 다음 가입자가 관리자 권한 획득. 인시던트(DB drop, 재해 복구 후 데이터 누락 등) 시 공격 표면이 됨.

**완화**:
1. `.env`에 환경별 토글 노출 (코드 변경 없이 운영자가 끌 수 있게)
2. README/배포 가이드에 "운영자 계정 생성 직후 `false`로 변경" 명시
3. 첫 가입자 promotion 시 `logger.info` 로그 (감사용)

위 3개 다 구현되어 있음. **가이드 문서에 반복 강조**가 가장 약한 부분 — `docs/QUALITY_SCORE.md`의 Authentication 섹션에서 운영 액션으로 적시.

### Mock user → real user 전이 스크립트의 idempotency
**상황**: `scripts/migrate_mock_to_real_user.py`(S6)가 운영 중 두 번 실행될 가능성. 한 번 transferred된 row를 다시 transfer하면 `user_id`가 교체될 수 있음.

**패턴**: `WHERE user_id = '00000000-0000-0000-0000-000000000001'`로 mock UUID에 한정. 실행 후 mock row 삭제 → 두 번째 실행 시 0 rows affected.

**규칙**: 데이터 이전 스크립트는 (1) 출처 row를 찾을 수 있는 정확한 predicate (2) 실행 종료 직전 출처 흔적 제거 (3) 부분 실패 시 재실행 가능한 idempotent 구조 — 셋 다 갖춰야 한다.

## Session 7 (2026-05-19) — Marketplace Resources Phase 1

### Pattern: strict xfail로 미구현/버그 spec 항목 pin
**상황**: 다른 팀원의 영역에서 spec 위반 발견 (예: `service.create_package_skill`이 `origin_kind` 미설정).

**패턴**:
1. 의도된 동작을 `@pytest.mark.xfail(reason="...", strict=True)`로 작성
2. 현재 동작을 별도 pinning test로 기록 (선택 사항)
3. "?" 프로토콜로 담당자에게 보고
4. 담당자 fix 시 strict xfail이 XPASS로 떨어져 자동 fail → 베조스가 promote (xfail 제거 + pinning test 삭제)

**효과**: 영역 침범 없이 spec drift를 자동 감지. Ralph Loop backpressure의 핵심 도구.

**예시**: `test_legacy_upload_package_should_set_imported_by_me` (M5 stage 4에서 자동 트리거됨).

### Pattern: ? 프로토콜로 cross-team boundary issue 보고
**상황**: 테스트 작성 중 발견한 회귀 또는 영역 경계 너머의 버그.

**패턴**:
- 짧은 "?" 마크 + file:line + 1줄 설명 → 담당자에게 SendMessage
- 사티아에게도 동시 보고 (release gate 결정자)
- 베조스는 그 영역 코드 수정하지 않고 다음 단계로 진행 (병렬화)

**예시**:
- `tests/test_executor.py:426` Stage 2 후 deprecated assertion → 젠슨에게 ?
- `install_service.install_item` lazy load on `acl_entries` → MissingGreenlet → 젠슨에게 ? (M9 발견)

### Pattern: M2.5 course correction (spec 위반 동기 정정)
**상황**: M2 listing 테스트 작성 중 service.py가 PRD §10.1 default filter 위반 발견 (`is_listed` filter mechanic만, default 미강제).

**패턴**:
1. 베조스가 발견 시점 보고 + 테스트는 현재 동작 그대로 (XPASS 가드)
2. 사티아가 "course correction" 태스크 생성 → 젠슨이 backend 정정
3. 동기 정정 후 베조스 테스트 정정 (예상 동작으로 재작성)

**효과**: 테스트가 spec 단순한 "구현 거울"이 아닌 **spec 검증 도구**로 작동. 베조스의 발견이 release gate 책임자(사티아)에게 즉시 escalate.

### Pattern: per-thread runtime root + selected-skill mount + credential injection + redaction (보안 4종)
**상황**: 마켓플레이스 도입으로 같은 사용자의 미선택 skill + 다른 사용자 skill 접근 가능성 (executor.py broad mount).

**패턴**:
1. `SkillToolContext(thread_id, output_dir, runtime_root, descriptors)` dataclass로 인자 폭증 방지
2. `build_skill_runtime_context(cfg, *, data_dir)` — per-thread `copytree(symlinks=False)`
3. `_create_skill_execute_tool(ctx)` — `Path(skill_dir).name`으로 slug 추출 → `ctx.descriptors` lookup이 보안 경계
4. `resolve_runtime_credentials(ctx, *, db, cfg)` — mapped env var만 주입, fail-fast 409
5. `redact_credential_values(text, mapped_env)` — subprocess result 자동 치환
6. `redact_keys(payload)` — SSE TOOL_CALL_START.parameters 구조적 마스킹
7. `cleanup_stale_runtime_roots(data_dir, retention_seconds=3600)` — mtime 기반 GC

**규칙**:
- redaction `\bsk-[A-Za-z0-9]{20,}\b`처럼 boundary 명시 (false-positive 방지)
- `len < 5` skip (placeholder/fragment 가드)
- 길이 정렬 (긴 값 먼저 치환 → 부분 매치 충돌 회피)

### Pattern: enumeration oracle envelope equality
**상황**: 비공개 item 접근 시도와 존재하지 않는 item 접근이 같은 응답이어야 함 (security.md).

**패턴**:
- status code뿐 아니라 JSON envelope shape까지 동등성 검증: `assert r_hidden.json() == r_missing.json()`
- 분기는 `logger.info(...)` 서버 로그에만 남기고 응답은 통일

### Pattern: 멀티 사용자 테스트 클라이언트
**상황**: 기본 `client` fixture가 super_user 강제 → ACL/권한 매트릭스 검증 불가.

**패턴**:
```python
async def _client_for_user(user: CurrentUser) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    async def _override() -> CurrentUser: return user
    app.dependency_overrides[get_current_user] = _override
    app.dependency_overrides[verify_csrf] = _no_csrf
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
```

재사용 위치: `test_marketplace_access.py`, `test_marketplace_listing.py`, `test_marketplace_e2e.py`.

### Failure: stage 2 후 기존 executor test deprecated
**상황**: `tests/test_executor.py:426` 가 `assert build_kwargs["skills"] == ["/skills/"]`로 정확한 값 검증 → Stage 2 패치 후 깨짐.

**교훈**: integration test가 production value를 hardcoded literal로 검증하면 production 변경마다 깨짐. **prefix/suffix 검사 또는 동적 변수 비교**로 작성해야 함.

```python
# 나쁨
assert build_kwargs["skills"] == ["/skills/"]
# 좋음
assert build_kwargs["skills"][0].startswith("/runtime/")
assert build_kwargs["skills"][0].endswith("/skills/")
# 또는
assert build_kwargs["skills"] == [f"/runtime/{cfg.thread_id}/skills/"]
```

### Failure: install_service lazy load on acl_entries (M9 발견)
**상황**: `install_service.install_item`이 `db.get(MarketplaceItem, ...)` 후 `can_install_item(item, user)` 호출. `can_install_item` → `can_view_item` → `item.acl_entries` lazy load → MissingGreenlet → 500 instead of 404.

**교훈**: 같은 ORM 모델의 relationship을 access predicate에서 사용하는 경우, **service-layer query는 catalog_service.get_item처럼 selectinload(MarketplaceItem.acl_entries) 사용 필수**. ``db.get`` 단축 경로는 access 예외 처리에서 위험.

**Status**: M9에서 strict xfail로 pin됨 (test_marketplace_e2e.py::TestScenario_10_4_RestrictedACL::test_restricted_acl_grants_and_denies). 젠슨 fix 대기.

### Pattern: chat-run-lifecycle 리뷰에서 도출 (2026-06-11)

**1. 상태 머신 전이는 잠금/CAS 하에서.** 여러 세션·태스크가 같은 row의 status를
read-modify-write 하면 stale read가 잘못된 전이(ValueError)나 lost update로 나타난다.
상태를 바꾸는 호출자는 `with_for_update`로 로드하거나 `UPDATE ... WHERE status IN (...)`
조건부 업데이트를 사용하고, 전이 함수 docstring에 동시성 계약을 명시한다.
(예: cancel이 `queued→canceling` 커밋 직후 worker가 `queued` 스냅샷으로 `running` 전이 시도 → canceled가 failed로 오분류)

**2. 장수명 스트림 훅에서 attach/send가 AbortController를 공유하면 소유권 가드 필수.**
effect가 진입 시 무조건 `abort()` 하면 진행 중인 스트림을 빼앗는다. in-flight ref 가드 +
"끝까지 소비한 run" 기록(consumedRunIdRef)으로 구분하고, cleanup에서는 guard 토큰을
무효화해 unmount 후 setState/콜백 실행을 차단한다. cleanup의 토큰 무효화는 반드시
`isStale(token)` 체크 뒤에 — 아니면 새 스트림의 토큰을 죽인다.

**3. 프로토콜 어댑터의 상관 ID(messageId/toolCallId)는 단일 소스에서 생성.**
이벤트 종류별로 다른 필드(data.id vs run_id)에서 ID를 뽑으면 START/CONTENT/END 매칭이
깨진다. 테스트는 "두 소스가 다른 값"인 케이스를 반드시 포함할 것.

**4. 회귀 테스트는 "수정 전 코드에서 실패하는가"로 검증.** 버그 수정 시 가드를 일시
무력화해 테스트가 정확히 실패하는지 확인 후 복원한다. 테스트가 mock 콜백(예:
onMessagesCommit)으로 갭을 가리면 실제 페이지 구성(콜백 없는)을 재현하는 케이스를 추가.

**5. ring buffer 기반 resume은 "after_id 미존재"를 silent gap으로 두지 말 것.**
`slice_events_after`는 after_id가 없으면 아무것도 yield하지 않는다 — 호출자가 이 의미를
해석해 stale 마커 + 전체 buffer replay로 degrade해야 한다 (클라이언트 dedup이 중복 처리).

**6. 콜백 기반 라이브러리를 async generator 로 bridge 할 때는 모든 settle 경로에서 종료 신호를 세울 것.**
`fetch-event-source` 는 signal abort 시 promise 를 reject 가 아니라 resolve 하고
onclose/onerror 도 호출하지 않는다. `.catch` 에서만 `closed=true` 를 세우면 abort 시
소비 루프가 영원히 대기하는 deadlock (Stop 후 isRunning 미해제의 근본 원인이었음).
종료 처리는 `.finally` 에 두고, abort 는 명시적으로 AbortError 로 변환해 소비자
계약을 유지한다. 라이브러리의 abort 의미론(reject? resolve? 콜백 호출?)을 가정하지
말고 소스로 확인할 것.

**7. E2E 의 webServer `reuseExistingServer` 는 포트 점유자가 "맞는 서버"인지 보장하지 않는다.**
docker-compose 컨테이너가 3000 을 점유하고 있으면 Playwright 가 그것을 프론트로
재사용해 전부 404 가 난다. 워크트리에서 E2E 는 `E2E_FRONTEND_PORT`/`E2E_BACKEND_PORT`
로 빈 포트를 지정해 자기 코드를 띄울 것. 또한 background 실행 시 출력을 tail 로
자르지 말 것 — 실패 진단에 서버 로그 전체가 필요하다.
