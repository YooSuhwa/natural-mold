# Builder + Assistant UI 디자인 스펙

| 항목 | 값 |
|------|-----|
| 작성자 | tim-cook |
| 날짜 | 2026-04-07 |
| 상태 | 제안됨 |
| 관련 문서 | ADR-005, moldy-agent-builder-spec-v2.md |

---

## 1. 개요

기존 4단계 대화형 에이전트 생성 UI(`conversational/page.tsx`)를 7단계 파이프라인 모니터링 Builder UI로,
기존 `fix-agent-dialog.tsx`의 간단한 다이얼로그를 풀사이즈 Assistant 대화 패널로 교체한다.

**디자인 원칙:**
- shadcn/ui 컴포넌트 우선 사용, 커스텀 UI 최소화
- 기존 프로젝트 디자인 시스템(colors, spacing, typography) 준수
- 다크/라이트 모드 모두 지원
- 모바일 반응형 고려 (최소 360px 뷰포트)

---

## 2. Builder UI (7단계 파이프라인)

### 2.1 전체 레이아웃

```
+---------------------------------------------------------------+
| [<] Agent Builder                              [Reset]        | <- Header
+---------------------------------------------------------------+
|                                                               |
|  [Phase 1: Input]                                             |
|  +-----------------------------------------------------------+|
|  | "어떤 에이전트를 만들고 싶으신가요?"                           ||
|  |                                                           ||
|  | +-------------------------------------------------------+ ||
|  | | textarea (자연어 입력)                                   | ||
|  | +-------------------------------------------------------+ ||
|  |                                          [시작하기 ->]    ||
|  +-----------------------------------------------------------+|
|                                                               |
|  [Phase Timeline — 7단계]                                     |
|  +-----------------------------------------------------------+|
|  | Phase 1: 프로젝트 초기화           [완료]                   ||
|  | Phase 2: 의도 분석                [진행 중]                 ||
|  | Phase 3: 도구 추천                [대기]                    ||
|  | Phase 4: 미들웨어 추천            [대기]                    ||
|  | Phase 5: 시스템 프롬프트 생성      [대기]                    ||
|  | Phase 6: 에이전트 설정            [대기]                    ||
|  | Phase 7: 최종 빌드                [대기]                    ||
|  +-----------------------------------------------------------+|
|                                                               |
|  [Phase Result Cards — 완료된 단계 결과]                       |
|  +-----------------------------------------------------------+|
|  | Intent 요약 카드 / 도구 추천 카드 / ...                     ||
|  +-----------------------------------------------------------+|
|                                                               |
|  [Phase 7: Final Confirmation]                                |
|  +-----------------------------------------------------------+|
|  | DraftAgentConfig 요약                                      ||
|  |                                          [에이전트 생성]    ||
|  +-----------------------------------------------------------+|
|                                                               |
+---------------------------------------------------------------+
```

### 2.2 입력 단계 (Phase 1 시작 전)

기존 `conversational/page.tsx`의 Phase 1 입력 영역을 재사용한다.

**컴포넌트:** `BuilderInputSection`

| 요소 | 스펙 |
|------|------|
| 질문 카드 | `rounded-xl border bg-background p-5`, MessageCircleIcon + 텍스트 |
| textarea | `min-h-[80px] max-h-[160px]`, placeholder: "뉴스를 요약하는 에이전트를 만들어줘" |
| 제출 버튼 | `Button size="lg"`, SendIcon + "시작하기" |
| 키보드 | Enter로 제출 (Shift+Enter 줄바꿈), IME composition 처리 |

**searchParams 지원:**
- `?initialMessage=...` query parameter로 대시보드에서 바로 입력값 전달 가능 (기존 패턴 유지)

### 2.3 Phase Timeline (7단계)

기존 `PhaseTimeline` 컴포넌트를 4단계에서 7단계로 확장한다.

**컴포넌트:** `BuilderTimeline`

```tsx
interface BuilderTimelineProps {
  currentPhase: number          // 0-7 (0=입력 전)
  phaseStatuses: PhaseStatus[]  // SSE로 실시간 업데이트
}

type PhaseStatus = {
  phase: number
  status: 'pending' | 'started' | 'completed' | 'failed'
  message?: string
}
```

**7단계 정의:**

| Phase | Label | Description | 아이콘 |
|-------|-------|-------------|--------|
| 1 | 프로젝트 초기화 | 작업 환경 준비 | FolderOpenIcon |
| 2 | 의도 분석 | 요청 분석 중 | BrainIcon |
| 3 | 도구 추천 | 적합한 도구 선정 | WrenchIcon |
| 4 | 미들웨어 추천 | 안정성/성능 계층 선정 | ShieldIcon |
| 5 | 시스템 프롬프트 | 에이전트 지침서 작성 | FileTextIcon |
| 6 | 에이전트 설정 | 최종 설정 통합 | SettingsIcon |
| 7 | 최종 빌드 | 에이전트 인스턴스 생성 | RocketIcon |

**상태별 시각 표현 (기존 패턴 확장):**

| 상태 | 아이콘 | 색상 | 배지 |
|------|--------|------|------|
| completed | CheckIcon (원형) | `bg-emerald-500 text-white` | `bg-emerald-100 text-emerald-700` |
| started | CircleDotIcon (원형) | `bg-primary text-primary-foreground` | `bg-primary/10 text-primary` |
| failed | XCircleIcon (원형) | `bg-destructive text-destructive-foreground` | `bg-destructive/10 text-destructive` |
| pending | ClockIcon (원형) | `border-muted-foreground/30 text-muted-foreground/50` | `bg-muted text-muted-foreground` |

**연결선:**
- 완료: `bg-emerald-500`
- 실패: `bg-destructive`
- 대기: `bg-muted-foreground/20`

**SSE 연결:**
- `GET /api/builder/{session_id}/stream` → `phase_progress` 이벤트로 실시간 업데이트
- `sub_agent_start` / `sub_agent_end` → Phase 2-5에서 서브에이전트 실행 상태 표시

**서브에이전트 실행 인디케이터:**
- Phase 2-5에 started 상태일 때, Phase label 아래에 작은 텍스트로 서브에이전트 이름 표시
- 예: "의도 분석 서브에이전트 실행 중..." (Loader2Icon animate-spin + text-xs text-muted-foreground)

### 2.4 Phase 결과 카드

각 Phase가 완료되면 결과를 카드로 표시한다. Phase Timeline 아래에 순서대로 쌓인다.

**컴포넌트:** `PhaseResultCard`

```tsx
interface PhaseResultCardProps {
  phase: number
  title: string
  children: React.ReactNode  // Phase별 커스텀 내용
  status: 'completed' | 'failed'
}
```

**공통 스타일:**
- `rounded-xl border bg-background` (기존 Card 스타일)
- 상단에 Phase 번호 배지 + 제목
- 완료 시 `border-emerald-500/20`, 실패 시 `border-destructive/20`
- 애니메이션: `animate-in fade-in slide-in-from-bottom-2 duration-300`

#### Phase 2 결과: Intent 요약 카드

**컴포넌트:** `IntentSummaryCard`

AgentCreationIntent의 핵심 필드를 요약 표시한다.

```
+-----------------------------------------------------------+
| [2] 의도 분석 완료                                [완료]    |
+-----------------------------------------------------------+
| 에이전트: News Summarizer (뉴스 요약 에이전트)              |
| 역할: 최신 뉴스를 검색하고 핵심 내용을 요약하여 전달        |
|                                                           |
| 핵심 작업: 뉴스 검색 및 요약                               |
| 응답 스타일: 간결한 요약 + 핵심 포인트                      |
|                                                           |
| 사용 사례:                                                |
|  - 일일 뉴스 브리핑                                       |
|  - 특정 주제 뉴스 모니터링                                 |
|  - 뉴스 비교 분석                                         |
+-----------------------------------------------------------+
```

**레이아웃:**
- `space-y-2.5 rounded-lg bg-muted/50 p-4 text-sm` (기존 draft info 스타일 재사용)
- 필드별 `flex justify-between` 또는 라벨 + 값 구조
- use_cases는 `ul` 리스트

#### Phase 3 결과: 도구 추천 카드

**컴포넌트:** `ToolRecommendationCards`

기존 `ToolCard` 패턴을 재사용한다.

```
+-----------------------------------------------------------+
| [3] 도구 추천 완료                                [완료]    |
+-----------------------------------------------------------+
| +-------------------------------------------------------+ |
| | [WrenchIcon] tavily_search                            | |
| | 범용 웹 검색. 최신 뉴스와 정보 검색에 적합               | |
| | 선정 이유: 뉴스 검색의 핵심 도구                        | |
| +-------------------------------------------------------+ |
| +-------------------------------------------------------+ |
| | [WrenchIcon] naver_news                               | |
| | 네이버 뉴스 검색. 한국어 뉴스 특화                       | |
| | 선정 이유: 한국 뉴스 커버리지 확대                       | |
| +-------------------------------------------------------+ |
+-----------------------------------------------------------+
```

**레이아웃:**
- 기존 `ToolCard` 확장: `reason` 필드 추가 (text-xs text-muted-foreground)
- `flex gap-3 rounded-xl border bg-background p-4`

#### Phase 4 결과: 미들웨어 추천 카드

**컴포넌트:** `MiddlewareRecommendationCards`

도구 카드와 동일한 레이아웃. 아이콘만 `ShieldIcon`으로 변경.

```
+-------------------------------------------------------+
| [ShieldIcon] ToolRetryMiddleware                      |
| 외부 API 호출 실패 시 자동 재시도                        |
| 선정 이유: 뉴스 API 호출 안정성 확보                     |
+-------------------------------------------------------+
```

#### Phase 5 결과: 시스템 프롬프트 미리보기

**컴포넌트:** `SystemPromptPreview`

기존 Phase 4의 `<details>` 패턴을 재사용하되 더 명시적으로 표현한다.

```
+-----------------------------------------------------------+
| [5] 시스템 프롬프트 생성 완료                     [완료]    |
+-----------------------------------------------------------+
| [v] 시스템 프롬프트 보기                                    |
| +-------------------------------------------------------+ |
| | # News Summarizer                                     | |
| | ## Role                                               | |
| | 최신 뉴스를 검색하고 핵심 내용을 요약하여...             | |
| | ...                                                   | |
| +-------------------------------------------------------+ |
|                                  약 3,200자 / 5,000자 상한 |
+-----------------------------------------------------------+
```

**레이아웃:**
- `<Collapsible>` (shadcn/ui) 사용 — 기본 접힘 상태
- 펼쳤을 때: `max-h-[300px] overflow-auto rounded-lg bg-muted p-4`
- `<MarkdownContent>` 컴포넌트로 렌더링 (기존 chat 패턴 재사용)
- 하단에 글자 수 표시 (text-xs text-muted-foreground)

#### Phase 6-7 결과: 최종 설정 요약

기존 Phase 4의 `DraftConfig` 표시 패턴을 재사용한다.

### 2.5 최종 확인 (Phase 7 완료 후)

**컴포넌트:** `BuilderConfirmation`

SSE에서 `build_preview` 이벤트를 수신하면 DraftAgentConfig를 표시한다.

```
+-----------------------------------------------------------+
| [SparklesIcon] 에이전트 설정 완료                           |
+-----------------------------------------------------------+
|                                                           |
| 에이전트 이름: News Summarizer                              |
| 한글 이름: 뉴스 요약 에이전트                                |
| 설명: 최신 뉴스를 검색하고...                               |
| 모델: anthropic:claude-sonnet-4-5                         |
|                                                           |
| 도구 (3개):                                               |
|  [WrenchIcon] tavily_search                               |
|  [WrenchIcon] naver_news                                  |
|  [WrenchIcon] naver_blog                                  |
|                                                           |
| 미들웨어 (2개):                                           |
|  [ShieldIcon] ToolRetryMiddleware                         |
|  [ShieldIcon] SummarizationMiddleware                     |
|                                                           |
| [v] 시스템 프롬프트 보기                                    |
|                                                           |
| +-------------------------------------------------------+ |
| |              [에이전트 생성하기]                         | |
| +-------------------------------------------------------+ |
+-----------------------------------------------------------+
```

**동작:**
- "에이전트 생성하기" 클릭 → `POST /api/builder/{session_id}/confirm`
- 성공 시 `/agents/{agent_id}` 로 리다이렉트
- 로딩 중 Loader2Icon animate-spin + 버튼 disabled

### 2.6 에러 상태

SSE에서 `error` 이벤트 수신 시:

**recoverable: true**
- Phase Timeline에서 해당 Phase를 `failed`로 표시
- 에러 메시지 카드 표시 (border-destructive/20)
- "다시 시도" 버튼 제공

**recoverable: false**
- 전체 빌드 실패 표시
- "처음부터 다시 시작" 버튼 (handleReset)

### 2.7 SSE 스트리밍 클라이언트

기존 `stream-chat.ts` 패턴을 확장한다.

**새 파일:** `frontend/src/lib/sse/stream-builder.ts`

```typescript
export async function* streamBuilder(
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<BuilderSSEEvent> {
  // GET /api/builder/{session_id}/stream
  // 이벤트 타입: phase_progress, sub_agent_start, sub_agent_end,
  //             build_preview, error
}
```

**이벤트 타입 (TypeScript):**

```typescript
type BuilderSSEEventType =
  | 'phase_progress'
  | 'sub_agent_start'
  | 'sub_agent_end'
  | 'build_preview'
  | 'error'

type BuilderSSEEvent =
  | { event: 'phase_progress'; data: { phase: number; status: string; message?: string } }
  | { event: 'sub_agent_start'; data: { phase: number; agent_name: string } }
  | { event: 'sub_agent_end'; data: { phase: number; result_summary: string } }
  | { event: 'build_preview'; data: { draft_config: DraftAgentConfig } }
  | { event: 'error'; data: { phase: number; message: string; recoverable: boolean } }
```

### 2.8 상태 관리

**Jotai atoms** (`frontend/src/lib/stores/builder-store.ts`):

```typescript
// 빌드 세션 상태
export const builderSessionIdAtom = atom<string | null>(null)
export const builderPhaseStatusesAtom = atom<PhaseStatus[]>(INITIAL_PHASES)
export const builderCurrentPhaseAtom = atom<number>(0)

// Phase 결과
export const builderIntentAtom = atom<AgentCreationIntent | null>(null)
export const builderToolsAtom = atom<ToolRecommendation[]>([])
export const builderMiddlewaresAtom = atom<MiddlewareRecommendation[]>([])
export const builderSystemPromptAtom = atom<string>('')
export const builderDraftConfigAtom = atom<DraftAgentConfig | null>(null)

// 에러
export const builderErrorAtom = atom<BuildErrorEvent | null>(null)

// 서브에이전트 상태 (Phase 2-5)
export const builderSubAgentAtom = atom<{ phase: number; name: string } | null>(null)
```

### 2.9 라우팅

| 경로 | 역할 |
|------|------|
| `/agents/new` | 생성 방법 선택 (기존 유지) |
| `/agents/new/builder` | Builder UI (신규) |
| `/agents/new/conversational` | 기존 대화형 (v2 완료 후 삭제) |

v2 마이그레이션 기간에는 두 경로가 공존한다.

### 2.10 반응형 레이아웃

| 뷰포트 | 동작 |
|--------|------|
| Desktop (1024px+) | `max-w-2xl mx-auto`, 기존 레이아웃 |
| Tablet (768-1023px) | `max-w-xl mx-auto`, 동일 레이아웃 |
| Mobile (360-767px) | `px-4`, Phase Timeline 텍스트 크기 축소, 카드 스택 |

---

## 3. Assistant UI (에이전트 설정 대화 패널)

### 3.1 전체 레이아웃

기존 `fix-agent-dialog.tsx`의 600px 다이얼로그를 에이전트 설정 페이지 내 전체 높이 패널로 교체한다.

**진입점:** 에이전트 설정 페이지(`/agents/{agentId}/settings`)에서 "AI Assistant" 탭 추가

```
+---------------------------------------------------------------+
| [<] 에이전트 설정 — My Agent                                    |
+---------------------------------------------------------------+
| [기본 정보] [모델] [도구] [트리거] [AI Assistant]               | <- 탭 추가
+---------------------------------------------------------------+
|                                                               |
|  Assistant 대화 영역                                           |
|  +-----------------------------------------------------------+|
|  |                                                           ||
|  | [빈 상태 / 대화 메시지]                                    ||
|  |                                                           ||
|  +-----------------------------------------------------------+|
|                                                               |
|  +-----------------------------------------------------------+|
|  | [입력 영역]                                                ||
|  +-----------------------------------------------------------+|
|                                                               |
+---------------------------------------------------------------+
```

### 3.2 탭 통합

기존 설정 페이지의 Tabs에 "AI Assistant" 탭을 추가한다.

```tsx
<TabsList>
  <TabsTrigger value="basic">{t('tabs.basic')}</TabsTrigger>
  <TabsTrigger value="model">{t('tabs.model')}</TabsTrigger>
  <TabsTrigger value="tools">{t('tabs.tools')}</TabsTrigger>
  <TabsTrigger value="triggers">{t('tabs.triggers')}</TabsTrigger>
  <TabsTrigger value="assistant">
    <SparklesIcon className="size-4 mr-1" />
    {t('tabs.assistant')}
  </TabsTrigger>
</TabsList>
```

**대안 (모바일):** 탭이 5개로 늘어나므로, 모바일에서는 스크롤 가능한 `TabsList`로 처리 (기존 `overflow-x-auto scrollbar-none` 스타일 활용).

### 3.3 빈 상태 (Empty State)

기존 `fix-agent-dialog.tsx`의 빈 상태 패턴을 확장한다.

```
+-----------------------------------------------------------+
|                                                           |
|            [SparklesIcon size-10 text-primary/30]          |
|                                                           |
|         AI Assistant로 에이전트를 수정하세요                 |
|     자연어로 요청하면 도구, 프롬프트, 모델 등을              |
|            자동으로 수정해 드립니다.                         |
|                                                           |
|    [좀 더 친근하게 말하도록 바꿔줘]                          |
|    [검색 도구를 추가해줘]                                   |
|    [비용을 줄이고 싶어]                                     |
|    [시스템 프롬프트를 개선해줘]                              |
|                                                           |
+-----------------------------------------------------------+
```

**Quick suggestion chips:**
- `rounded-full border px-3 py-1.5 text-xs hover:bg-accent transition-colors cursor-pointer`
- 클릭 시 입력 필드에 텍스트 삽입 (기존 패턴)

### 3.4 대화 영역

기존 `fix-agent-dialog.tsx`의 메시지 렌더링을 확장한다.

**컴포넌트:** `AssistantChatArea`

#### 사용자 메시지

```
                                              +------------------+
                                              | 검색 도구 추가해줘 |
                                              +------------------+
                                                           [UserIcon]
```

- 기존 패턴: `bg-primary text-primary-foreground rounded-2xl px-3.5 py-2`
- 우측 정렬

#### 어시스턴트 메시지

```
[BotIcon]
+-----------------------------------------------------------+
| tavily_search 도구를 추가했습니다.                           |
| 시스템 프롬프트에도 도구 사용 가이드를 추가했습니다.           |
+-----------------------------------------------------------+
```

- 기존 패턴: `bg-muted rounded-2xl px-3.5 py-2.5`
- `<MarkdownContent>` 사용 (마크다운 렌더링 지원)
- 좌측 정렬, BotIcon 아바타

#### 도구 실행 결과 인라인 표시

Assistant가 도구를 실행하면 (add_tool, remove_tool, edit_system_prompt 등), SSE 스트리밍 중에 도구 실행 결과를 인라인으로 표시한다.

**컴포넌트:** `AssistantToolAction`

```
[BotIcon]
+-----------------------------------------------------------+
| [도구 실행 결과]                                            |
| +-------------------------------------------------------+ |
| | [+] tavily_search 추가됨                    [성공 배지] | |
| +-------------------------------------------------------+ |
| +-------------------------------------------------------+ |
| | [~] 시스템 프롬프트 수정됨                   [성공 배지] | |
| +-------------------------------------------------------+ |
|                                                           |
| tavily_search 도구를 추가하고, 시스템 프롬프트에            |
| 도구 사용 가이드를 추가했습니다.                             |
+-----------------------------------------------------------+
```

**도구 액션 배지:**

| 액션 | 아이콘 | 배지 색상 |
|------|--------|----------|
| 도구 추가 | PlusIcon | `bg-emerald-500/10 text-emerald-600` |
| 도구 제거 | MinusIcon | `bg-orange-500/10 text-orange-600` |
| 프롬프트 수정 | PencilIcon | `bg-blue-500/10 text-blue-600` |
| 프롬프트 교체 | RefreshCwIcon | `bg-blue-500/10 text-blue-600` |
| 미들웨어 추가 | PlusIcon + ShieldIcon | `bg-emerald-500/10 text-emerald-600` |
| 미들웨어 제거 | MinusIcon + ShieldIcon | `bg-orange-500/10 text-orange-600` |
| 모델 변경 | CpuIcon | `bg-purple-500/10 text-purple-600` |
| 스케줄 생성/수정 | CalendarIcon | `bg-indigo-500/10 text-indigo-600` |
| 조회 (읽기 전용) | EyeIcon | `bg-muted text-muted-foreground` |

**성공/실패 표시:**
- 성공: `CheckCircle2Icon text-emerald-500` + "성공"
- 실패: `XCircleIcon text-destructive` + 에러 메시지

**SSE 매핑 (기존 chat SSE 이벤트 재사용):**
- `tool_call_start` → 도구 이름 + args 표시 (로딩 상태)
- `tool_call_result` → 결과 업데이트 (성공/실패)
- `content_delta` → 텍스트 스트리밍
- `message_end` → 최종 메시지 확정

#### 프롬프트 Diff 표시

`edit_system_prompt` 도구 실행 결과에 diff를 표시한다.

**컴포넌트:** `PromptDiffDisplay`

```
+-------------------------------------------------------+
| [~] 시스템 프롬프트 수정됨                              |
| - "적절히 대응하세요"                                   |  <- 빨간 배경
| + "다음 단계를 따라 처리하세요: 1. ..."                  |  <- 초록 배경
+-------------------------------------------------------+
```

**스타일:**
- 삭제 행: `bg-destructive/10 text-destructive line-through`
- 추가 행: `bg-emerald-500/10 text-emerald-700`
- 접을 수 있는 `<Collapsible>` — 기본 펼침, 긴 diff는 접힘

### 3.5 Clarifying Question (ask_clarifying_question)

기존 `OptionCard` 패턴을 재사용한다.

Assistant가 `ask_clarifying_question` 도구를 호출하면, 선택지 카드를 표시한다.

```
[BotIcon]
+-----------------------------------------------------------+
| 어떤 범위의 수정을 원하시나요?                               |
+-----------------------------------------------------------+

+-----------------------------------------------------------+
| ( ) 시스템 프롬프트만 개선                                   |
+-----------------------------------------------------------+
+-----------------------------------------------------------+
| ( ) 도구와 미들웨어도 함께 최적화                            |
+-----------------------------------------------------------+
+-----------------------------------------------------------+
| ( ) 전체 설정을 처음부터 재검토                              |
+-----------------------------------------------------------+
+-----------------------------------------------------------+
| ( ) 직접 입력                                               |
+-----------------------------------------------------------+

                                              [보내기 ->]
```

**동작:**
- 옵션 클릭 → 선택 하이라이트 (단일 선택)
- "직접 입력" 선택 시 → textarea 표시
- "보내기" 클릭 → 선택된 옵션 텍스트를 메시지로 전송
- 기존 `OptionCard` 컴포넌트 그대로 재사용 (`multiSelect: false`)

### 3.6 입력 영역

기존 `ChatInput` 컴포넌트를 재사용한다.

**차이점:**
- 파일 첨부(PaperclipIcon) 비활성 (Assistant는 파일 미지원)
- 모델 표시 불필요 (기존 ChatInput의 modelName prop 생략)
- 토큰 사용량 표시 (SSE `message_end` 의 usage 데이터)

### 3.7 SSE 스트리밍

기존 `stream-chat.ts`를 그대로 재사용한다.

**API:** `POST /api/agents/{agent_id}/assistant/message`

```typescript
// stream-chat.ts의 streamChat() 패턴과 동일
// POST body: { content: string }
// SSE 이벤트: message_start, content_delta, tool_call_start,
//             tool_call_result, message_end, error
```

기존 SSE 이벤트 타입과 완전히 동일하므로 별도 스트리밍 함수 불필요.
`streamChat()` 함수에 엔드포인트 URL만 다르게 전달하면 된다.

```typescript
// 또는 streamAssistant 래퍼
export async function* streamAssistant(
  agentId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  // POST /api/agents/{agentId}/assistant/message
  // 나머지는 streamChat과 동일
}
```

### 3.8 상태 관리

**Jotai atoms** (`frontend/src/lib/stores/assistant-store.ts`):

```typescript
// 메시지 히스토리
export const assistantMessagesAtom = atom<AssistantMessage[]>([])

// 스트리밍 상태
export const assistantStreamingAtom = atom<boolean>(false)
export const assistantStreamingContentAtom = atom<string>('')
export const assistantToolActionsAtom = atom<AssistantToolAction[]>([])

// Clarifying question
export const assistantClarifyingQuestionAtom = atom<ClarifyingQuestion | null>(null)
```

```typescript
interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
  toolActions?: AssistantToolAction[]  // 도구 실행 결과
}

interface AssistantToolAction {
  toolName: string
  summary: string
  success: boolean
  diff?: { old: string; new: string }  // edit_system_prompt 용
}

interface ClarifyingQuestion {
  question: string
  options: string[]  // 3개 + "직접 입력"
}
```

### 3.9 반응형 레이아웃

| 뷰포트 | 동작 |
|--------|------|
| Desktop (1024px+) | 탭 내 `max-w-2xl mx-auto`, 대화 영역 고정 높이 |
| Tablet (768-1023px) | 동일 |
| Mobile (360-767px) | 탭 내 전체 너비, 대화 영역 `flex-1` |

---

## 4. 컴포넌트 목록

### 4.1 Builder 컴포넌트 (신규)

| 컴포넌트 | 경로 | 설명 |
|----------|------|------|
| `BuilderPage` | `app/agents/new/builder/page.tsx` | Builder 전체 페이지 |
| `BuilderInputSection` | `components/builder/builder-input.tsx` | 자연어 입력 |
| `BuilderTimeline` | `components/builder/builder-timeline.tsx` | 7단계 타임라인 |
| `PhaseResultCard` | `components/builder/phase-result-card.tsx` | Phase 결과 카드 래퍼 |
| `IntentSummaryCard` | `components/builder/intent-summary-card.tsx` | Phase 2 결과 |
| `ToolRecommendationCards` | `components/builder/tool-recommendation-cards.tsx` | Phase 3 결과 |
| `MiddlewareRecommendationCards` | `components/builder/middleware-recommendation-cards.tsx` | Phase 4 결과 |
| `SystemPromptPreview` | `components/builder/system-prompt-preview.tsx` | Phase 5 결과 |
| `BuilderConfirmation` | `components/builder/builder-confirmation.tsx` | 최종 확인 |

### 4.2 Assistant 컴포넌트 (신규)

| 컴포넌트 | 경로 | 설명 |
|----------|------|------|
| `AssistantTab` | `app/agents/[agentId]/settings/_components/assistant-tab.tsx` | 설정 페이지 탭 |
| `AssistantChatArea` | `components/assistant/assistant-chat-area.tsx` | 대화 영역 |
| `AssistantToolAction` | `components/assistant/assistant-tool-action.tsx` | 도구 실행 결과 |
| `PromptDiffDisplay` | `components/assistant/prompt-diff-display.tsx` | 프롬프트 diff |
| `ClarifyingQuestionCard` | `components/assistant/clarifying-question-card.tsx` | 선택지 카드 |

### 4.3 재사용 컴포넌트 (기존)

| 컴포넌트 | 원본 | 재사용 위치 |
|----------|------|------------|
| `OptionCard` | `conversational/page.tsx` | Assistant ClarifyingQuestion |
| `ToolCard` | `conversational/page.tsx` | Builder Phase 3 |
| `ChatInput` | `components/chat/chat-input.tsx` | Assistant 입력 |
| `MarkdownContent` | `components/chat/markdown-content.tsx` | Builder, Assistant |
| `StreamingMessage` | `components/chat/streaming-message.tsx` | Assistant (패턴 참조) |
| `PhaseTimeline` | `conversational/page.tsx` | Builder (확장) |

**리팩토링 필요:** `OptionCard`, `ToolCard`를 `conversational/page.tsx`에서 추출하여 `components/shared/` 로 이동해야 한다. Builder와 Assistant 양쪽에서 재사용하기 위함.

---

## 5. 접근성 (a11y)

### WCAG 2.1 AA 준수 사항

| 항목 | 요구사항 | 구현 |
|------|----------|------|
| 키보드 네비게이션 | 모든 인터랙티브 요소 Tab 접근 가능 | `tabIndex`, `focus-visible` 스타일 |
| 스크린 리더 | Phase 상태 변경 알림 | `aria-live="polite"` on Timeline |
| 색상 대비 | 4.5:1 이상 | 기존 shadcn/ui 토큰 준수 |
| 포커스 관리 | Phase 완료 시 결과 카드로 포커스 이동 | `useEffect` + `ref.focus()` |
| IME 지원 | 한글 입력 시 Enter 오작동 방지 | `isComposingRef` (기존 패턴) |
| 에러 알림 | 빌드 실패 시 알림 | `role="alert"` on error card |
| 로딩 상태 | 스크린 리더에 로딩 전달 | `aria-busy="true"`, `aria-label` |

### Phase Timeline aria 속성

```tsx
<div role="list" aria-label="빌드 진행 상황">
  <div role="listitem" aria-current={status === 'started' ? 'step' : undefined}>
    <span aria-label={`Phase ${phase.id}: ${phase.label}, ${statusLabel}`} />
  </div>
</div>
```

---

## 6. 다크/라이트 모드

모든 색상은 CSS 변수 기반 (기존 shadcn/ui 시스템 그대로):

| 용도 | 라이트 | 다크 |
|------|--------|------|
| 완료 배지 | `bg-emerald-100 text-emerald-700` | `bg-emerald-500/20 text-emerald-400` |
| 에러 배지 | `bg-destructive/10 text-destructive` | 동일 (CSS 변수) |
| 도구 추가 | `bg-emerald-500/10 text-emerald-600` | 동일 (opacity 기반) |
| 도구 제거 | `bg-orange-500/10 text-orange-600` | 동일 (opacity 기반) |
| 프롬프트 diff+ | `bg-emerald-500/10 text-emerald-700` | `text-emerald-400` |
| 프롬프트 diff- | `bg-destructive/10 text-destructive` | 동일 |

opacity 기반 색상(`/10`, `/20`)은 다크/라이트 모두에서 잘 작동한다.

---

## 7. 애니메이션

| 요소 | 애니메이션 | 구현 |
|------|-----------|------|
| Phase 완료 전환 | fade + scale | `animate-in fade-in duration-200` |
| 결과 카드 등장 | slide up + fade | `animate-in fade-in slide-in-from-bottom-2 duration-300` |
| 서브에이전트 스피너 | spin | `Loader2Icon animate-spin` |
| 스트리밍 커서 | pulse | `animate-pulse bg-primary/60` (기존 패턴) |
| 도구 실행 배지 | fade in | `animate-in fade-in duration-200` |

모든 애니메이션은 `prefers-reduced-motion: reduce` 미디어 쿼리를 존중한다 (Tailwind 기본 지원).

---

## 8. i18n 키 구조

### Builder

```
agent.builder.header
agent.builder.initialQuestion
agent.builder.initialPlaceholder
agent.builder.startButton
agent.builder.resetButton
agent.builder.cancelButton
agent.builder.cancelConfirm
agent.builder.cancelDescription
agent.builder.progress
agent.builder.phase1.label ~ phase7.label
agent.builder.phase1.description ~ phase7.description
agent.builder.status.completed / active / pending / failed
agent.builder.loadingText
agent.builder.confirmTitle
agent.builder.createAgent
agent.builder.draftName / draftDescription / draftModel
agent.builder.includedTools
agent.builder.includedMiddlewares
agent.builder.viewSystemPrompt
agent.builder.error.sessionFailed / generic / buildFailed
agent.builder.phaseLogCompleted
```

### Assistant

```
agent.settings.tabs.assistant
agent.assistant.emptyState
agent.assistant.emptyDescription
agent.assistant.suggestion.polite / addSearch / cost / improvePrompt
agent.assistant.inputPlaceholder
agent.assistant.toolAction.added / removed / modified / replaced
agent.assistant.toolAction.success / failed
agent.assistant.clarifying.submit
agent.assistant.clarifying.customInput
agent.assistant.error.generic
agent.assistant.toast.applied / failed
```

---

## 9. 구현 우선순위

| 순서 | 컴포넌트 | 이유 |
|------|----------|------|
| 1 | `BuilderTimeline` | 핵심 UI, Phase 표시 |
| 2 | `BuilderInputSection` | 기존 코드 재사용, 빠름 |
| 3 | Phase 결과 카드들 | SSE 연동 전 정적 UI |
| 4 | `stream-builder.ts` | SSE 클라이언트 |
| 5 | `BuilderPage` (통합) | 전체 페이지 조립 |
| 6 | `BuilderConfirmation` | 최종 확인 |
| 7 | `AssistantTab` | 설정 페이지 탭 통합 |
| 8 | `AssistantChatArea` | 대화 영역 |
| 9 | `AssistantToolAction` | 도구 실행 결과 |
| 10 | `ClarifyingQuestionCard` | OptionCard 재사용 |

---

## 10. 기존 코드 마이그레이션 체크리스트

- [ ] `OptionCard`를 `components/shared/option-card.tsx`로 추출
- [ ] `ToolCard`를 `components/shared/tool-card.tsx`로 추출
- [ ] `PhaseTimeline`을 `components/shared/phase-timeline.tsx`로 추출 후 7단계 확장
- [ ] `/agents/new` 페이지에 "AI Builder" 옵션 추가 (기존 "대화형 생성" 옆)
- [ ] 설정 페이지 Tabs에 "AI Assistant" 탭 추가
- [ ] `fix-agent-dialog.tsx` → v2 Assistant 완료 후 삭제
- [ ] `conversational/page.tsx` → v2 Builder 완료 후 삭제
