# UX Improvement Design Spec

> **Author:** Tim Cook (TTH Designer/UX)
> **Date:** 2026-04-07
> **Status:** Draft
> **Target:** Moldy AI Agent Builder — Frontend UI/UX 전체 개선

---

## 목차

1. [Coming Soon 패턴](#1-coming-soon-패턴)
2. [에이전트 카드 리디자인](#2-에이전트-카드-리디자인)
3. [설정 페이지 탭 구조](#3-설정-페이지-탭-구조)
4. [브레드크럼 디자인](#4-브레드크럼-디자인)
5. [앱 설정 페이지](#5-앱-설정-페이지)
6. [도구 상세 Dialog](#6-도구-상세-dialog)

---

## 디자인 원칙

| 원칙 | 설명 |
|------|------|
| **Simplicity** | 불필요한 것을 제거하면 본질이 드러난다 |
| **Consistency** | 같은 패턴은 같은 방식으로. Dialog는 Dialog, Badge는 Badge |
| **Accessibility** | WCAG 2.1 AA 기준. focus-visible, aria-label, 4.5:1 대비비 |
| **Progressive Disclosure** | 필요할 때 필요한 정보만. 호버/탭으로 점진적 노출 |

### 오퍼시티 전략

두 가지 오퍼시티 패턴을 용도별로 구분한다:

| 패턴 | 클래스 | 용도 |
|------|--------|------|
| **Dim (반투명)** | `opacity-50 hover:opacity-70` | 기능 존재하나 미출시 (Coming Soon) |
| **Show/Hide** | `opacity-0 group-hover:opacity-100` | 기능 존재하나 시각적 노이즈 줄임 (카드 액션 버튼) |

Dim은 "있지만 아직 안 됨", Show/Hide는 "있지만 필요할 때만 보여줌".

---

## 1. Coming Soon 패턴

### 문제

현재 `disabled` 버튼(`opacity-40 cursor-not-allowed`)은 왜 비활성인지 사용자에게 알려주지 않는다. 클릭해도 아무 반응이 없어 UX가 막혀 있는 느낌을 준다.

### 해결

`disabled` 속성을 제거하고, 클릭 시 `toast.info`로 "준비 중" 메시지를 보여준다. 시각적으로는 "곧 출시"임을 암시하되, 인터랙션은 살아있다.

### 시각 디자인

```
┌─────────────────────────────┐
│  [📎] ← opacity-50, 클릭 가능  │
│        커서: pointer           │
│        호버 시 opacity-70      │
│        클릭 → toast.info       │
└─────────────────────────────┘
```

### Tailwind 클래스 가이드

```tsx
// 공통 유틸리티 클래스 (모든 Coming Soon 요소에 적용)
const COMING_SOON_CLASSES = [
  "opacity-50",              // 기본 상태: 반투명
  "hover:opacity-70",        // 호버: 약간 선명해짐 (인터랙션 힌트)
  "cursor-pointer",          // 클릭 가능함을 표시
  "transition-opacity",      // 부드러운 전환
  "duration-200",            // 200ms 트랜지션
].join(" ");
```

### 컴포넌트 구조 (JSX 스케치)

```tsx
// components/shared/coming-soon-button.tsx
"use client";

import { Button, type ButtonProps } from "@/components/ui/button";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

interface ComingSoonButtonProps extends Omit<ButtonProps, "onClick" | "disabled"> {
  featureKey?: string;   // i18n 키 (예: "fileAttach")
  children: React.ReactNode;
}

export function ComingSoonButton({
  featureKey,
  children,
  className,
  ...props
}: ComingSoonButtonProps) {
  const t = useTranslations("common.comingSoon");

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    toast.info(featureKey ? t(featureKey) : t("default"));
    // default: "이 기능은 준비 중입니다"
  };

  return (
    <Button
      {...props}
      onClick={handleClick}
      className={cn(
        "opacity-50 hover:opacity-70 cursor-pointer transition-opacity duration-200",
        className
      )}
    >
      {children}
    </Button>
  );
}
```

### 사용 예시

```tsx
// Before (기존)
<Button disabled className="opacity-40 cursor-not-allowed">
  <PaperclipIcon className="size-4" />
</Button>

// After (개선)
<ComingSoonButton variant="ghost" size="icon-sm" featureKey="fileAttach">
  <PaperclipIcon className="size-4" />
</ComingSoonButton>
```

### shadcn/ui 컴포넌트

- `Button` (variant: ghost)
- `toast.info` (sonner)

### 접근성

- `aria-label`에 "(준비 중)" 포함
- `disabled` 제거 → 키보드 포커스 가능
- toast는 `role="status"`로 스크린리더에 전달됨

### 다크모드

- `opacity-50/70`은 테마 불문 동작. 추가 다크모드 클래스 불필요.

### 반응형

- 버튼 사이즈는 기존 `size` prop 따름. 별도 반응형 처리 불필요.

---

## 2. 에이전트 카드 리디자인

### 문제

1. 도구 이름을 쉼표로 나열(`tools.map(t => t.name).join(', ')`)하여 카드 높이가 들쭉날쭉
2. 설정/비주얼설정 버튼이 항상 표시되어 시각적 노이즈
3. 에이전트 설명보다 메타데이터(도구 목록)가 더 눈에 띔

### 해결

```
┌──────────────────────────────────────┐
│  ⭐ Agent Name                [active]│  ← 이름 + 상태 배지 + 즐겨찾기
│                                      │
│  에이전트 설명 텍스트가 여기에       │  ← 설명 강조 (2줄 제한)
│  최대 2줄까지 표시됩니다...          │
│                                      │
│  🤖 GPT-4o  ·  🔧 3 tools           │  ← 모델 + 도구 개수 배지
│──────────────────────────────────────│
│  2026-03-15            [⚙️][🎨][⭐]  │  ← 호버 시에만 액션 버튼 표시
└──────────────────────────────────────┘
```

### Tailwind 클래스 가이드

```tsx
// 카드 컨테이너
"h-full transition-colors hover:border-primary/40 group"

// 카드 설명 (강조)
"text-sm text-muted-foreground line-clamp-2 min-h-[2.5rem]"
// min-h로 설명 없는 카드도 동일 높이 확보

// 메타 정보 (모델 + 도구 배지)
"flex items-center gap-2 text-xs text-muted-foreground"

// 도구 개수 배지
"inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium"

// 액션 버튼 (호버 시에만)
"opacity-0 group-hover:opacity-100 transition-opacity duration-200"
// 키보드 포커스 시에도 표시
"focus-within:opacity-100"
```

### 컴포넌트 구조 (JSX 스케치)

```tsx
// components/agent/agent-card.tsx
<Link href={`/agents/${agent.id}`}>
  <Card className="h-full transition-colors hover:border-primary/40 group">
    <CardHeader className="pb-2">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <CardTitle className="truncate group-hover:text-primary transition-colors">
            {agent.name}
          </CardTitle>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <FavoriteButton agent={agent} />
          <Badge variant={statusVariant}>{statusLabel}</Badge>
        </div>
      </div>

      {/* 설명 강조 — 고정 높이로 카드 균일화 */}
      <p className="text-sm text-muted-foreground line-clamp-2 min-h-[2.5rem]">
        {agent.description || t("noDescription")}
      </p>
    </CardHeader>

    <CardContent className="pt-0">
      {/* 모델 + 도구 개수 (간결) */}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        {agent.model && (
          <span className="flex items-center gap-1">
            <CpuIcon className="size-3.5" />
            {agent.model.display_name}
          </span>
        )}
        {agent.tools.length > 0 && (
          <span className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 font-medium">
            <WrenchIcon className="size-3" />
            {agent.tools.length}
          </span>
        )}
      </div>
    </CardContent>

    <CardFooter>
      <div className="flex w-full items-center justify-between">
        <span className="text-xs text-muted-foreground">
          {formattedDate}
        </span>

        {/* 호버 시에만 액션 버튼 표시 */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity duration-200">
          <Button variant="ghost" size="icon-sm" asChild>
            <Link href={`/agents/${agent.id}/visual`} onClick={stopProp}>
              <WorkflowIcon className="size-4" />
            </Link>
          </Button>
          <Button variant="ghost" size="icon-sm" asChild>
            <Link href={`/agents/${agent.id}/settings`} onClick={stopProp}>
              <Settings2Icon className="size-4" />
            </Link>
          </Button>
        </div>
      </div>
    </CardFooter>
  </Card>
</Link>
```

### 핵심 변경 요약

| 영역 | Before | After |
|------|--------|-------|
| 도구 표시 | 이름 나열 (가변 높이) | 개수 배지 `🔧 3` (고정 높이) |
| 설명 | `line-clamp-2` | `line-clamp-2` + `min-h-[2.5rem]` |
| 액션 버튼 | 항상 표시 | `opacity-0 group-hover:opacity-100` |
| 즐겨찾기 | 액션 영역에 혼재 | 이름 옆 독립 위치 |
| 카드 높이 | 도구 수에 따라 가변 | 균일 (min-height 보장) |

### shadcn/ui 컴포넌트

- `Card`, `CardHeader`, `CardTitle`, `CardContent`, `CardFooter`
- `Badge` (상태 표시)
- `Button` (variant: ghost, size: icon-sm)

### 반응형

- 그리드: `grid gap-4 sm:grid-cols-2 lg:grid-cols-3` (기존 유지)
- 모바일(1col): 액션 버튼 항상 표시 (`@media (hover: none)` → `opacity-100`)

```tsx
// 터치 디바이스에서는 항상 표시
"opacity-0 group-hover:opacity-100 focus-within:opacity-100 touch:opacity-100"
// Tailwind v4: @media (hover: none) { opacity: 1 }
```

### 다크모드

- `bg-muted` 배지는 테마 자동 대응
- `text-muted-foreground`도 테마 자동 대응
- 추가 다크모드 클래스 불필요

### 접근성

- 호버로 숨긴 버튼은 `focus-within:opacity-100`으로 키보드 접근 보장
- `aria-label` 필수: "설정", "비주얼 설정"
- 카드 전체가 `<Link>` — 내부 버튼은 `onClick={e => e.stopPropagation()}`

---

## 3. 설정 페이지 탭 구조

### 문제

565줄짜리 단일 스크롤 페이지. 인지 부하가 높고, 원하는 섹션을 찾기 어렵다.

### 해결

4개 탭으로 분리 + 하단 sticky 저장 바.

```
┌─────────────────────────────────────────────┐
│  ← Back          Agent Name 설정             │
│─────────────────────────────────────────────│
│  [기본정보] [모델] [도구·스킬] [트리거]       │  ← Tabs (sticky)
│─────────────────────────────────────────────│
│                                             │
│  (탭 콘텐츠 영역 — 스크롤)                   │
│                                             │
│                                             │
│─────────────────────────────────────────────│
│  [🗑 삭제]                        [💾 저장]  │  ← sticky 바
└─────────────────────────────────────────────┘
```

### 탭 분할

| 탭 | 내용 | 해당 섹션 (기존 라인) |
|----|------|----------------------|
| **기본정보** | 이름, 설명, 시스템 프롬프트 | L181-205 |
| **모델** | 모델 선택, Temperature, Top P, Max Tokens, 리셋 | L207-283 |
| **도구·스킬** | 도구 체크리스트, 스킬 체크리스트, 미들웨어 | L285-391 |
| **트리거** | 기존 트리거 목록, 새 트리거 추가 폼 | L393-520 |

### Tailwind 클래스 가이드

```tsx
// 탭 리스트 (sticky)
"sticky top-0 z-10 bg-background/95 backdrop-blur-sm border-b"

// 탭 콘텐츠 영역
"flex-1 overflow-auto py-6"

// 각 탭 콘텐츠 내부
"mx-auto w-full max-w-2xl space-y-6"

// 하단 sticky 저장 바
"sticky bottom-0 border-t bg-background/95 backdrop-blur-sm px-6 py-3"

// 저장 바 레이아웃
"mx-auto flex w-full max-w-2xl items-center justify-between"
```

### 컴포넌트 구조 (JSX 스케치)

```tsx
// app/agents/[agentId]/settings/page.tsx (리팩토링 후)
export default function AgentSettingsPage() {
  const [activeTab, setActiveTab] = useState("basic");

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* 헤더 */}
      <div className="px-6 pt-6 pb-4">
        <BackButton />
        <PageHeader title={`${agent.name} ${t("title")}`} />
      </div>

      {/* 탭 네비게이션 (sticky) */}
      {/* i18n: useTranslations("agent.settings") → t("tabs.basic") = "agent.settings.tabs.basic" */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <div className="sticky top-0 z-10 bg-background/95 backdrop-blur-sm border-b px-6">
          <TabsList className="w-full justify-start">
            <TabsTrigger value="basic">{t("tabs.basic")}</TabsTrigger>
            <TabsTrigger value="model">{t("tabs.model")}</TabsTrigger>
            <TabsTrigger value="tools">{t("tabs.tools")}</TabsTrigger>
            <TabsTrigger value="triggers">{t("tabs.triggers")}</TabsTrigger>
          </TabsList>
        </div>

        {/* 탭 콘텐츠 (스크롤) */}
        <div className="flex-1 overflow-auto px-6 py-6">
          <div className="mx-auto w-full max-w-2xl">
            <TabsContent value="basic">
              <BasicInfoTab />
            </TabsContent>
            <TabsContent value="model">
              <ModelTab />
            </TabsContent>
            <TabsContent value="tools">
              <ToolsSkillsTab />
            </TabsContent>
            <TabsContent value="triggers">
              <TriggersTab />
            </TabsContent>
          </div>
        </div>
      </Tabs>

      {/* 하단 sticky 저장 바 */}
      <div className="sticky bottom-0 border-t bg-background/95 backdrop-blur-sm px-6 py-3">
        <div className="mx-auto flex w-full max-w-2xl items-center justify-between">
          <DeleteAgentButton agentId={agentId} />
          <Button onClick={handleSave} disabled={isSaving}>
            {isSaving ? <Loader2Icon className="size-4 animate-spin" /> : <SaveIcon className="size-4" />}
            {t("save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
```

### 파일 분리 가이드

```
app/agents/[agentId]/settings/
├── page.tsx                    # 탭 컨테이너 + 저장 바 (~80줄)
├── _components/
│   ├── basic-info-tab.tsx      # 이름, 설명, 시스템 프롬프트 (~60줄)
│   ├── model-tab.tsx           # 모델 선택, 파라미터 슬라이더 (~100줄)
│   ├── tools-skills-tab.tsx    # 도구/스킬/미들웨어 체크리스트 (~120줄)
│   └── triggers-tab.tsx        # 트리거 목록 + 추가 폼 (~150줄)
```

### shadcn/ui 컴포넌트

- `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent`
- 기존 사용 중인 모든 컴포넌트 유지 (Badge, Button, Input, Textarea, Slider 등)

### 반응형

- 탭: 모바일에서 `overflow-x-auto`로 수평 스크롤 허용
- 저장 바: 모바일에서 `flex-col gap-2` → 버튼 세로 배치

```tsx
// 모바일 탭 스크롤
"overflow-x-auto scrollbar-none"

// 모바일 저장 바
"flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between"
```

### 다크모드

- `bg-background/95 backdrop-blur-sm`은 테마 자동 대응
- `border-b`, `border-t`도 테마 변수 사용으로 자동 대응

### 접근성

- `Tabs`는 shadcn/ui가 aria-role 자동 처리 (`role="tablist"`, `role="tab"`, `role="tabpanel"`)
- 키보드: 좌우 화살표로 탭 전환
- 저장 단축키 고려: `Ctrl+S` / `Cmd+S` 바인딩 (선택사항)

### 상태 관리 주의사항

- 탭 전환 시 **폼 상태 유지** 필수. 각 탭은 상위 컴포넌트의 state를 공유.
- `useState`를 page.tsx에 유지하고, 각 탭 컴포넌트에 props로 전달.
- 또는 `useReducer`로 폼 상태 통합 관리.

---

## 4. 브레드크럼 디자인

### 문제

1. 현재 `app-header.tsx`에 `Separator(orientation="vertical")`만 있고 경로 정보 없음
2. 사용자가 현재 위치를 파악하기 어려움
3. 깊은 경로(에이전트 → 설정)에서 뒤로가기가 불편

### 해결

경로 기반 자동 생성 브레드크럼. Separator(세로선) 제거.

```
┌─────────────────────────────────────────────────┐
│  [≡]  홈 / 에이전트 / MyBot / 설정              │
└─────────────────────────────────────────────────┘
```

### 경로 매핑

| URL 패턴 | 브레드크럼 |
|----------|-----------|
| `/` | 홈 |
| `/agents/[id]` | 홈 / 에이전트 / {agent.name} |
| `/agents/[id]/chat` | 홈 / 에이전트 / {agent.name} / 채팅 |
| `/agents/[id]/settings` | 홈 / 에이전트 / {agent.name} / 설정 |
| `/tools` | 홈 / 도구 |
| `/models` | 홈 / 모델 |
| `/usage` | 홈 / 사용량 |
| `/settings` | 홈 / 설정 |

### Tailwind 클래스 가이드

```tsx
// 브레드크럼 컨테이너
"flex items-center gap-1.5 text-sm"

// 브레드크럼 아이템 (링크)
"text-muted-foreground hover:text-foreground transition-colors duration-200"

// 현재 페이지 (마지막 아이템)
"text-foreground font-medium truncate max-w-[200px]"

// 구분자 (chevron)
"text-muted-foreground/60 size-3.5"
```

### 컴포넌트 구조 (JSX 스케치)

```tsx
// components/layout/breadcrumb-nav.tsx
"use client";

import { usePathname } from "next/navigation";
import { ChevronRightIcon, HomeIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

// 정적 경로 매핑
const ROUTE_LABELS: Record<string, string> = {
  agents: "nav.agents",
  tools: "nav.tools",
  models: "nav.models",
  usage: "nav.usage",
  settings: "nav.settings",
  chat: "nav.chat",
  create: "nav.create",
};

export function BreadcrumbNav() {
  const pathname = usePathname();
  const t = useTranslations();
  const segments = pathname.split("/").filter(Boolean);

  if (segments.length === 0) return null; // 홈에서는 숨김

  const crumbs = segments.map((segment, index) => {
    const href = "/" + segments.slice(0, index + 1).join("/");
    const isLast = index === segments.length - 1;
    const isId = /^[0-9a-f-]+$/.test(segment); // UUID 감지

    return { segment, href, isLast, isId };
  });

  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm">
      {/* 홈 아이콘 */}
      <Link
        href="/"
        className="text-muted-foreground hover:text-foreground transition-colors"
      >
        <HomeIcon className="size-4" />
      </Link>

      {crumbs.map(({ segment, href, isLast, isId }) => (
        <Fragment key={href}>
          <ChevronRightIcon className="size-3.5 text-muted-foreground/60" />
          {isLast ? (
            <span className="text-foreground font-medium truncate max-w-[200px]">
              {isId ? <AgentName id={segment} /> : t(ROUTE_LABELS[segment] ?? segment)}
            </span>
          ) : (
            <Link
              href={href}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              {isId ? <AgentName id={segment} /> : t(ROUTE_LABELS[segment] ?? segment)}
            </Link>
          )}
        </Fragment>
      ))}
    </nav>
  );
}
```

### app-header.tsx 변경

```tsx
// Before
export function AppHeader() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="mr-2 h-4" />
    </header>
  );
}

// After
export function AppHeader() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
      <SidebarTrigger />
      <BreadcrumbNav />
    </header>
  );
}
```

### shadcn/ui 컴포넌트

- `Separator` 제거
- 커스텀 `BreadcrumbNav` 컴포넌트 (shadcn에 Breadcrumb가 있으면 활용 가능하나, @base-ui에 없으므로 직접 구현)

### 반응형

- 모바일: 마지막 2 세그먼트만 표시 + `...` 축약

```tsx
// 모바일 축약 (3개 이상 세그먼트)
"hidden sm:flex"      // 중간 세그먼트 숨김
"flex sm:hidden"      // 축약(…) 표시
```

### 다크모드

- `text-muted-foreground`, `text-foreground`는 테마 자동 대응
- 추가 다크모드 클래스 불필요

### 접근성

- `<nav aria-label="Breadcrumb">` 랜드마크
- 현재 페이지: `aria-current="page"` 추가
- 구분자: `aria-hidden="true"` (스크린리더 무시)

### 동적 이름 해결

에이전트 ID(UUID)가 경로에 있을 때 이름으로 표시:
- TanStack Query 캐시에서 에이전트 이름 조회
- 캐시 없으면 ID 축약 표시 (`agent-abc...`)

---

## 5. 앱 설정 페이지

### 문제

앱 전체 설정(테마, 언어, 프로필)을 관리할 페이지가 없다.

### 해결

카드 기반 설정 레이아웃. 각 설정 그룹을 독립 카드로 분리.

```
┌─────────────────────────────────────────────┐
│  앱 설정                                     │
│─────────────────────────────────────────────│
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  👤 프로필                           │    │
│  │  이름: Mock User                     │    │
│  │  이메일: mock@moldy.ai              │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ┌─────────────┐  ┌─────────────────────┐   │
│  │  🎨 테마     │  │  🌐 언어            │   │
│  │             │  │                     │   │
│  │  ○ Light   │  │  ● 한국어           │   │
│  │  ● Dark    │  │  ○ English          │   │
│  │  ○ System  │  │                     │   │
│  └─────────────┘  └─────────────────────┘   │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  🔑 API 키 관리                      │    │
│  │  OpenAI: ●●●●●●●●sk-...abc         │    │
│  │  Anthropic: 설정되지 않음     [설정]  │    │
│  └─────────────────────────────────────┘    │
│                                             │
└─────────────────────────────────────────────┘
```

### Tailwind 클래스 가이드

```tsx
// 페이지 컨테이너
"flex flex-1 flex-col gap-6 overflow-auto p-6"

// 콘텐츠 영역
"mx-auto w-full max-w-2xl space-y-6"

// 설정 카드
"rounded-xl ring-1 ring-foreground/10 bg-card p-6"

// 카드 제목
"flex items-center gap-2 text-base font-semibold"

// 설정 항목 행
"flex items-center justify-between py-3"

// 구분선
"border-t border-foreground/5"

// 2열 그리드 (테마 + 언어)
"grid gap-4 sm:grid-cols-2"

// 라디오 옵션
"flex items-center gap-3 rounded-lg border p-3 cursor-pointer hover:bg-accent transition-colors duration-200"
"data-[state=checked]:border-primary data-[state=checked]:bg-primary/5"
```

### 컴포넌트 구조 (JSX 스케치)

```tsx
// app/settings/page.tsx
export default function SettingsPage() {
  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader title={t("title")} description={t("description")} />

      <div className="mx-auto w-full max-w-2xl space-y-6">
        {/* 프로필 카드 (전체 폭) */}
        <ProfileCard />

        {/* 테마 + 언어 (2열) */}
        <div className="grid gap-4 sm:grid-cols-2">
          <ThemeCard />
          <LanguageCard />
        </div>

        {/* API 키 관리 (전체 폭) */}
        <ApiKeysCard />
      </div>
    </div>
  );
}
```

```tsx
// 테마 카드 예시
function ThemeCard() {
  const { theme, setTheme } = useTheme();
  const t = useTranslations("settings.theme");

  const themes = [
    { value: "light", label: t("light"), icon: SunIcon },
    { value: "dark", label: t("dark"), icon: MoonIcon },
    { value: "system", label: t("system"), icon: MonitorIcon },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <PaletteIcon className="size-4" />
          {t("title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {themes.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            onClick={() => setTheme(value)}
            className={cn(
              "flex w-full items-center gap-3 rounded-lg border p-3",
              "cursor-pointer hover:bg-accent transition-colors",
              theme === value && "border-primary bg-primary/5"
            )}
          >
            <Icon className="size-4" />
            <span className="text-sm font-medium">{label}</span>
          </button>
        ))}
      </CardContent>
    </Card>
  );
}
```

### shadcn/ui 컴포넌트

- `Card`, `CardHeader`, `CardTitle`, `CardContent`
- `Button`
- `Input` (프로필 편집 시)
- `PageHeader`

### 반응형

- 2열 그리드 → 모바일 1열: `grid gap-4 sm:grid-cols-2`
- API 키 값: 모바일에서 `truncate` + 툴팁

### 다크모드

- 테마 토글이 이 페이지에 있으므로 즉각 반영 확인 필요
- `bg-primary/5`는 테마 자동 대응

### 접근성

- 테마/언어 선택: `role="radiogroup"` + `role="radio"` + `aria-checked`
- 또는 시맨틱 `<fieldset>` + `<input type="radio">`
- API 키: 마스킹된 값에 `aria-label="API key (hidden)"`
- 각 카드: `<section aria-labelledby="section-title-id">` 랜드마크
- 포커스 순서: 프로필 → 테마 → 언어 → API 키 (시각적 순서와 일치)
- 테마 변경 시 `aria-live="polite"` 영역에 "테마가 변경되었습니다" 안내

---

## 6. 도구 상세 Dialog

### 문제

도구 상세가 `Sheet`(사이드바)로 열리지만, 다른 모든 상세/편집 UI는 `Dialog`(중앙 모달)를 사용. 일관성이 깨짐.

### 해결

`Sheet` → `Dialog`로 변경. 기존 콘텐츠 구조는 유지.

```
┌──────────────────────────────────────────┐
│                                          │
│   ┌──────────────────────────────────┐   │
│   │  [×]                             │   │
│   │  Web Search (DuckDuckGo)         │   │
│   │  DuckDuckGo 검색 엔진으로...     │   │
│   │                                  │   │
│   │  타입: prebuilt    태그: search   │   │
│   │                                  │   │
│   │  ┌──────────────────────────┐    │   │
│   │  │  파라미터                 │    │   │
│   │  │  query  string  필수     │    │   │
│   │  │  limit  integer 선택     │    │   │
│   │  └──────────────────────────┘    │   │
│   │                                  │   │
│   │  인증: 서버 키 설정됨 ✓         │   │
│   │                                  │   │
│   └──────────────────────────────────┘   │
│                                          │
└──────────────────────────────────────────┘
```

### Tailwind 클래스 가이드

```tsx
// Dialog 콘텐츠 (중앙 모달)
"sm:max-w-lg max-h-[85vh] overflow-auto"

// 섹션 간격
"space-y-5 pt-4"

// 메타데이터 배지 영역
"flex flex-wrap gap-2"

// 파라미터 테이블
"rounded-lg border divide-y"

// 테이블 행
"flex items-center px-3 py-2 text-sm"

// 테이블 셀
"flex-1" // 이름
"w-20 text-muted-foreground" // 타입
"w-16 text-right" // 필수/선택
```

### 컴포넌트 구조 (JSX 스케치)

```tsx
// Before (Sheet)
<Sheet open={!!detailTool} onOpenChange={(open) => { if (!open) setDetailTool(null) }}>
  <SheetContent className="sm:max-w-lg overflow-auto">
    <SheetHeader>
      <SheetTitle>{detailTool.name}</SheetTitle>
      <SheetDescription>{detailTool.description}</SheetDescription>
    </SheetHeader>
    {/* ... 콘텐츠 ... */}
  </SheetContent>
</Sheet>

// After (Dialog)
<Dialog open={!!detailTool} onOpenChange={(open) => { if (!open) setDetailTool(null) }}>
  <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-auto">
    <DialogHeader>
      <DialogTitle>{detailTool.name}</DialogTitle>
      <DialogDescription>{detailTool.description}</DialogDescription>
    </DialogHeader>
    {/* ... 콘텐츠 (동일) ... */}
  </DialogContent>
</Dialog>
```

### 변경 범위

| 변경 | Before | After |
|------|--------|-------|
| 컴포넌트 | `Sheet` | `Dialog` |
| import | `SheetContent`, `SheetHeader`, `SheetTitle`, `SheetDescription` | `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogDescription` |
| 클래스 | `sm:max-w-lg overflow-auto` | `sm:max-w-lg max-h-[85vh] overflow-auto` |
| 위치 | 우측 사이드바 (슬라이드) | 중앙 모달 (페이드+줌) |
| 콘텐츠 | 변경 없음 | 변경 없음 |

### shadcn/ui 컴포넌트

- `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogDescription`
- `Badge` (타입, 태그 표시 — 기존 유지)

### 반응형

- `sm:max-w-lg`: 데스크탑에서 제한된 너비
- 모바일: Dialog가 전체 너비로 확장 (shadcn Dialog 기본 동작)
- `max-h-[85vh] overflow-auto`: 콘텐츠 길 때 스크롤

### 다크모드

- Dialog 컴포넌트가 테마 자동 대응 (bg-background, text-foreground)
- 추가 다크모드 클래스 불필요

### 접근성

- Dialog는 모달 포커스 트랩 자동 제공 (Sheet과 동일)
- `Escape` 키로 닫기 (기존과 동일)
- `aria-labelledby`, `aria-describedby` 자동 연결
- 파라미터 테이블: `role="table"` + `role="row"` + `role="cell"` 또는 시맨틱 `<table>`
- 닫기 버튼: `aria-label="닫기"` (DialogContent의 X 버튼에 자동 적용됨)
- 열기 시 첫 포커스: DialogTitle로 이동 (기본 동작)

---

## 구현 우선순위

| 순서 | 항목 | 난이도 | 영향도 | 담당 스토리 |
|------|------|--------|--------|------------|
| 1 | Coming Soon 패턴 | 🟢 낮음 | 중 | S4 |
| 2 | 도구 상세 Dialog | 🟢 낮음 | 중 | S3 |
| 3 | 에이전트 카드 리디자인 | 🟡 중간 | 높 | S4 |
| 4 | 브레드크럼 디자인 | 🟡 중간 | 높 | S5 |
| 5 | 설정 페이지 탭 구조 | 🔴 높음 | 높 | S7 |
| 6 | 앱 설정 페이지 | 🟡 중간 | 중 | S6 |

---

## 공통 참고사항

### Tailwind v4 주의

- `@apply` 대신 유틸리티 클래스 직접 사용 권장
- CSS 변수 기반 테마: `bg-background`, `text-foreground` 등은 `@theme` 블록에서 정의
- `dark:` 접두사 대신 CSS 변수가 테마 자동 전환

### shadcn/ui (@base-ui 기반) 주의

- `data-[state=...]` 대신 `data-[...]` 속성 사용하는 경우 있음
- 구현 전 `frontend/src/components/ui/` 해당 컴포넌트 코드 확인 필수
- `Trigger` → `TabsTrigger` (shadcn), 네이밍이 다를 수 있음

### 트랜지션 가이드

| 용도 | Duration | Easing |
|------|----------|--------|
| 호버 색상 변경 | 150ms | ease-out |
| 오퍼시티 전환 | 200ms | ease-out |
| 모달/시트 진입 | 200ms | ease-out |
| 탭 전환 | 150ms | ease-out |

### i18n 키 구조

```json
{
  "common": {
    "comingSoon": {
      "default": "이 기능은 준비 중입니다",
      "fileAttach": "파일 첨부 기능은 준비 중입니다"
    }
  },
  "settings": {
    "title": "설정",
    "tabs": {
      "basic": "기본정보",
      "model": "모델",
      "tools": "도구·스킬",
      "triggers": "트리거"
    },
    "theme": {
      "title": "테마",
      "light": "라이트",
      "dark": "다크",
      "system": "시스템"
    },
    "language": {
      "title": "언어"
    }
  },
  "nav": {
    "agents": "에이전트",
    "tools": "도구",
    "models": "모델",
    "usage": "사용량",
    "settings": "설정",
    "chat": "채팅",
    "create": "새로 만들기"
  }
}
```
