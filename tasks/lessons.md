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
