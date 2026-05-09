# Lessons — Cumulative Patterns (across sessions)

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
