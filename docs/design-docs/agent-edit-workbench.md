# Agent Edit Workbench — Design Spec

**Status**: Active
**Owner**: 사티아 (PO) / 저커버그 (구현)
**Plan**: `~/.claude/plans/image-41-ticklish-sky.md`
**Created**: 2026-04-28

---

## 1. 목표

`/agents/[agentId]/settings`를 **단일 통합 워크벤치**로 재설계.
- 좌측: 폼/비주얼 토글로 에이전트 설정 편집
- 우측: Fix·테스트·오프너·스케줄·설정 5탭 패널 (영구 표시)
- 헤더: 이름/설명 인라인 편집 + 작은 아바타 + 저장/삭제

---

## 2. 페이지 트리

```
AgentSettingsPage (page.tsx)
├── Header (인라인)
│   ├── BackButton
│   ├── AgentAvatar (sm)
│   ├── InlineInput name (ghost)
│   ├── InlineInput description (ghost)
│   ├── DeleteButton
│   └── SaveButton (with isDirty)
└── MainGrid (lg:grid-cols-2 stack on mobile)
    ├── LeftPanel
    │   ├── Tabs [폼 | 비주얼]
    │   └── TabsContent
    │       ├── 'form' → FormMode
    │       │   ├── SectionInstructions (collapsible, fullscreen, char-count)
    │       │   ├── SectionSubAgents (행 + [⚙])
    │       │   ├── SectionModel (행 + [⚙])
    │       │   └── ToolsMiddlewaresGrid (2col)
    │       │       ├── ToolsBox ([+도구], 행 리스트)
    │       │       └── MiddlewaresBox ([+미들웨어], 행 리스트)
    │       └── 'visual' → VisualSettingsFlow (inline, ReactFlowProvider)
    └── RightPanel
        ├── Tabs [Fix | 테스트 | 오프너 | 스케줄 | 설정]
        └── TabsContent
            ├── 'fix' → AssistantPanel (showHeader=false)
            ├── 'test' → TestChatPanel (신규)
            ├── 'opener' → OpenerEditor (신규)
            ├── 'schedule' → TriggersTab (재사용)
            └── 'settings' → SettingsPanel (이미지 전용)

Dialogs (state-controlled)
├── ModelDialog (모델 선택 + 파라미터 슬라이더)
├── SubAgentsDialog (skills 선택)
├── AddToolModal (tools 선택)
└── AddMiddlewareModal (middlewares 선택)
```

---

## 3. 헤더 인라인 편집 패턴

```tsx
<header className="flex items-start gap-3 border-b px-6 py-4">
  <Button variant="ghost" size="icon-sm" onClick={handleBack}>
    <ArrowLeftIcon className="size-4" />
  </Button>
  <AgentAvatar imageUrl={imageUrl} name={name} size="sm" />
  <div className="flex-1 min-w-0 space-y-0.5">
    <Input
      value={name}
      onChange={(e) => onNameChange(e.target.value)}
      className="border-0 bg-transparent px-0 text-lg font-semibold shadow-none focus-visible:ring-0 focus-visible:bg-muted/40"
      placeholder={t('namePlaceholder')}
    />
    <Input
      value={description}
      onChange={(e) => onDescriptionChange(e.target.value)}
      className="border-0 bg-transparent px-0 text-xs text-muted-foreground shadow-none focus-visible:ring-0 focus-visible:bg-muted/40"
      placeholder={t('descriptionPlaceholder')}
    />
  </div>
  <div className="flex items-center gap-2">
    <DeleteAlertDialog ... />
    <Button onClick={handleSave} disabled={!isDirty || isPending}>
      {isPending ? <Loader2Icon className="mr-1 size-4 animate-spin" /> : null}
      {t('save')}
    </Button>
  </div>
</header>
```

핵심: ghost variant로 input을 plain text처럼 보이게 하다가 focus/hover 시 muted 배경으로 편집 가능 표시.

---

## 4. 좌측 폼 모드 섹션 패턴

### 4.1 공통 섹션 헤더 (행 패턴)

```tsx
<div className="flex items-center justify-between rounded-md border px-4 py-3">
  <div className="flex items-center gap-2 min-w-0">
    <Icon className="size-4 text-muted-foreground" />
    <span className="text-sm font-medium">{label}</span>
    <span className="text-sm text-muted-foreground truncate">{summary}</span>
  </div>
  <Button variant="ghost" size="icon-sm" onClick={openDialog}>
    <SettingsIcon className="size-4" />
  </Button>
</div>
```

### 4.2 지침 섹션 (collapsible + fullscreen)

```tsx
<Collapsible defaultOpen>
  <div className="flex items-center justify-between">
    <CollapsibleTrigger className="flex items-center gap-1">
      <ChevronDownIcon /> 지침
    </CollapsibleTrigger>
    <Button variant="ghost" size="icon-sm" onClick={() => setFullscreen(true)}>
      <MaximizeIcon className="size-4" />
    </Button>
  </div>
  <CollapsibleContent>
    <Textarea value={systemPrompt} onChange={...} rows={12} className="font-mono text-xs" />
    <div className="text-right text-xs text-muted-foreground">{count}자</div>
  </CollapsibleContent>
</Collapsible>

<Dialog open={fullscreen} onOpenChange={setFullscreen}>
  <DialogContent className="max-w-5xl">
    <Textarea value={systemPrompt} onChange={...} className="h-[70vh]" />
  </DialogContent>
</Dialog>
```

### 4.3 도구·미들웨어 그리드 (2칸)

```tsx
<div className="grid grid-cols-1 gap-4 md:grid-cols-2">
  <ToolsBox tools={...} selectedIds={...} onAdd={openAddToolModal} onRemove={...} />
  <MiddlewaresBox middlewares={...} selectedTypes={...} onAdd={openAddMiddlewareModal} onRemove={...} />
</div>
```

각 박스 구조:
```tsx
<div className="rounded-md border">
  <div className="flex items-center justify-between border-b px-4 py-2">
    <CollapsibleTrigger className="flex items-center gap-1">
      <ChevronDownIcon /> {label}
    </CollapsibleTrigger>
    <Button size="sm" onClick={onAdd}>
      <PlusIcon className="size-4" /> {addLabel}
    </Button>
  </div>
  <div className="divide-y">
    {selected.map(item => (
      <div className="flex items-center justify-between px-4 py-2">
        <div className="flex items-center gap-2">
          <ItemIcon /> <span>{item.name}</span>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" onClick={() => onConfig(item)}>
            <SettingsIcon className="size-3.5" />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={() => onRemove(item)}>
            <Trash2Icon className="size-3.5" />
          </Button>
        </div>
      </div>
    ))}
    {selected.length === 0 && (
      <div className="px-4 py-6 text-center text-sm text-muted-foreground">
        {emptyLabel}
      </div>
    )}
  </div>
</div>
```

---

## 5. 다이얼로그 명세

### 5.1 ModelDialog

```ts
interface ModelDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  modelId: string
  onModelIdChange: (v: string) => void
  temperature: number
  onTemperatureChange: (v: number) => void
  topP: number
  onTopPChange: (v: number) => void
  maxTokens: number
  onMaxTokensChange: (v: number) => void
  onReset: () => void
}
```
- 콘텐츠: 기존 `ModelTab` 콘텐츠 그대로 (ModelSelect + 슬라이더 3개 + 리셋 버튼)
- 닫기 시 changes는 이미 페이지 state에 반영됨(controlled)

### 5.2 SubAgentsDialog

```ts
interface SubAgentsDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  selectedSkillIds: Set<string>
  onToggleSkill: (id: string) => void
}
```
- 콘텐츠: `useSkills` 훅 + Checkbox 리스트 (현 `tools-skills-tab.tsx` skills 영역 재사용)
- 비어있으면 `/skills` 라우트로 링크

### 5.3 AddToolModal / AddMiddlewareModal

동일 패턴:
- 검색 입력(선택사항) + 카테고리 분류(선택사항)
- Checkbox 리스트
- 이미 선택된 항목은 "추가됨" 뱃지 + Checkbox checked
- 모달 닫기 시 selection은 페이지 state에 반영됨

---

## 6. 우측 패널 명세

### 6.1 RightPanel (탭 컨테이너)

```tsx
<Tabs value={tab} onValueChange={setTab}>
  <TabsList className="border-b bg-background sticky top-0 z-10">
    <TabsTrigger value="fix"><WrenchIcon /> Fix 에이전트</TabsTrigger>
    <TabsTrigger value="test"><MessageSquareIcon /> 테스트</TabsTrigger>
    <TabsTrigger value="opener"><HelpCircleIcon /> 오프너</TabsTrigger>
    <TabsTrigger value="schedule"><ClockIcon /> 스케줄</TabsTrigger>
    <TabsTrigger value="settings"><SettingsIcon /> 설정</TabsTrigger>
  </TabsList>
  <TabsContent value="fix"><AssistantPanel showHeader={false} ... /></TabsContent>
  <TabsContent value="test"><TestChatPanel ... /></TabsContent>
  <TabsContent value="opener"><OpenerEditor questions onChange ... /></TabsContent>
  <TabsContent value="schedule"><TriggersTab agentId onRequestDelete ... /></TabsContent>
  <TabsContent value="settings"><SettingsPanel agentId imageUrl name ... /></TabsContent>
</Tabs>
```

### 6.2 TestChatPanel (신규)

- 일반 에이전트 채팅. 대화 히스토리는 세션 내 로컬 state로만 관리(서버 저장 X)
- 초기 구현: 기존 `streamAssistant`(Fix용) 대신 일반 conversation 스트림 사용
- 빈 화면 empty state에 `agent.opener_questions` 버튼 표시 → 클릭 시 composer 텍스트 주입(전송 X)

### 6.3 OpenerEditor (신규)

```ts
interface OpenerEditorProps {
  questions: string[]
  onChange: (questions: string[]) => void
  max?: number  // default 12
}
```

레이아웃:
- 헤더: "사용자가 대화를 시작할 수 있는 예시 질문을 설정하세요" + `n/12` 카운터 + `[+ 추가]` 버튼
- 행: 번호 + Input(1~200자) + `[🗑]`
- 빈 상태: "예시 질문이 없습니다" + 추가 버튼

### 6.4 SettingsPanel (이미지 전용)

```tsx
<div className="flex flex-col items-center gap-4 p-6">
  <AgentAvatar imageUrl={imageUrl} name={name} size="xl" />
  <Button onClick={generate} disabled={isPending}>
    {imageUrl ? '이미지 재생성' : '이미지 생성'}
  </Button>
  {imageUrl && (
    <Button variant="ghost" onClick={remove}>
      이미지 제거
    </Button>
  )}
</div>
```

---

## 7. State 흐름

페이지 컴포넌트 (`settings/page.tsx`)에 모든 form state 보유:

```ts
const [name, setName] = useState('')
const [description, setDescription] = useState('')
const [systemPrompt, setSystemPrompt] = useState('')
const [modelId, setModelId] = useState('')
const [selectedToolIds, setSelectedToolIds] = useState<Set<string>>(new Set())
const [selectedSkillIds, setSelectedSkillIds] = useState<Set<string>>(new Set())
const [temperature, setTemperature] = useState(0.7)
const [topP, setTopP] = useState(1.0)
const [maxTokens, setMaxTokens] = useState(4096)
const [selectedMiddlewareTypes, setSelectedMiddlewareTypes] = useState<Set<string>>(new Set())
const [openerQuestions, setOpenerQuestions] = useState<string[]>([])  // 신규
```

다이얼로그·모달·우측 패널은 모두 controlled — 페이지 state를 직접 조작.

`isDirty` = 위 모든 값을 원본(`agent` from useAgent)과 비교.

저장은 단일 `[저장]` 버튼 → `useUpdateAgent.mutate({...all fields})`.

---

## 8. 새 채팅 빈 화면 오프너 표시

새 채팅 진입 시 `agent.opener_questions`가 있으면 empty state에 버튼 그룹으로 렌더:

```tsx
<div className="flex flex-wrap justify-center gap-2">
  {agent.opener_questions?.map((q) => (
    <button
      key={q}
      onClick={() => composer.setText(q)}  // 전송 X, 입력창에만 주입
      className="rounded-full border px-3 py-1 text-xs hover:bg-accent"
    >
      {q}
    </button>
  ))}
</div>
```

`useComposer` 훅(assistant-ui) 활용. `setText`로 composer 입력값 설정만 하고 submit은 사용자 액션에 맡김.

---

## 9. i18n 키

```jsonc
{
  "agent": {
    "settings": {
      "tabs": {
        "form": "폼", "visual": "비주얼",
        "fix": "Fix 에이전트", "test": "테스트", "opener": "오프너",
        "schedule": "스케줄", "settings": "설정"
      },
      "subAgents": "서브에이전트",
      "subAgentsEmpty": "서브에이전트가 없습니다",
      "model": "모델",
      "tools": "도구함",
      "addTool": "+ 도구",
      "middlewares": "미들웨어",
      "addMiddleware": "+ 미들웨어",
      "instructionFullscreen": "전체화면",
      "characterCount": "{count}자"
    },
    "opener": {
      "title": "오프너",
      "description": "사용자가 대화를 시작할 수 있는 예시 질문을 설정하세요",
      "counter": "{count}/{max}",
      "add": "+ 추가",
      "placeholder": "예시 질문 입력",
      "empty": "예시 질문이 없습니다",
      "maxReached": "최대 {max}개까지 추가할 수 있습니다"
    },
    "image": {
      "generate": "이미지 생성",
      "regenerate": "이미지 재생성",
      "remove": "이미지 제거"
    }
  }
}
```

---

## 10. 검증 시나리오

1. 라우트 `/agents/{id}/settings` 진입 → 좌(폼)/우(Fix) 분할
2. 헤더 이름/설명 변경 → [저장] 활성
3. [폼] → [비주얼] → ReactFlow 그래프 렌더
4. 행 [⚙] → 다이얼로그 → 변경 → 닫기 → 행 요약 갱신
5. [+도구] → 모달 → 체크 → 닫기 → 좌측 그리드 좌측에 행 추가
6. [+미들웨어] → 모달 → 체크 → 닫기 → 좌측 그리드 우측에 행 추가
7. 행 [🗑] → 즉시 제거(저장 전까지 page state만)
8. 우측 [오프너] → 항목 추가/삭제/순서 → [저장]
9. 우측 [설정] → 이미지 생성/재생성/제거
10. 새 대화 진입 → empty state에 오프너 버튼 → 클릭 → 입력창 주입(전송 X)
11. 미저장 [←] → confirm
