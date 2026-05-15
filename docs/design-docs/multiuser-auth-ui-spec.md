# Multi-User Auth — UI/UX Spec

- **Status**: Accepted (2026-05-08)
- **Author**: tim-cook
- **Scope**: Phase 7 (프론트엔드 인증) — `~/.claude/plans/replicated-crunching-lark.md` 기반
- **연관 문서**: `ADR-010-ui-tokens-and-dialog-shell.md` (토큰 시스템), `progress.txt` (API 계약)
- **승인된 결정**: Email + Password (MVP), HttpOnly Cookie + CSRF, 첫 가입자 자동 super_user

이 문서는 저커버그(프론트엔드 구현자)가 추가 디자인 결정 없이 그대로 구현할 수 있도록 작성되었다.
모든 컴포넌트는 shadcn/ui 기존 토큰만 사용한다 — 새 토큰을 만들지 않는다.

---

## 1. 디자인 원칙

### 1.1 단순함이 곧 명품
- **인증은 마찰 없이 빠르게 통과해야 한다.** 사용자는 "로그인"이 목표가 아니라 "에이전트 만들기"가 목표다.
- 로그인 페이지의 첫 인상은 **3초 안에** 결정된다 — 필드 2개, 버튼 1개, 명확한 위계.
- 하단 보조 링크(회원가입/비밀번호 찾기)는 시각적 무게를 낮게 (text link, no border).
- 한 페이지에 한 가지 행동만 — 광고/장식 금지.

### 1.2 디자인 언어 일관성
- **사용 컴포넌트(shadcn/ui)**: `Card`, `Form`, `Input`, `Button`, `Checkbox`, `Label`, `Dialog`(via `DialogShell`), `Avatar`, `DropdownMenu`, `Toast`(`sonner`), `Alert`.
- **레이아웃 메트릭**: 카드 패딩 `p-8` (모바일 `p-6`), 필드 간격 `space-y-4`, 폼 내부 라벨↔입력 `space-y-1.5`. ADR-010의 DialogShell 메트릭과 동일한 리듬.
- **버튼 우선순위**: primary(`Button` default) = 주 액션, ghost/link = 보조 액션. 다이얼로그 푸터 우측 정렬 규칙 동일.

### 1.3 한국어 우선, 영어 fallback
- 모든 라벨/메시지/에러는 **한국어 1차**로 작성하고 i18n 키 구조는 기존 `t('user.name')` 패턴(`app-sidebar.tsx`)을 따른다.
- 신규 키 네임스페이스: `auth.*`, `auth.errors.*`, `auth.onboarding.*`.
- 영어 fallback은 i18n 리소스의 `en` 번들에 둠 (existing 컨벤션).

### 1.4 다크모드 호환
- shadcn 토큰만 사용하므로 자동 호환된다 — 새 색상 정의 없음.
- 단 다음만 명시적으로 지킬 것:
  - 폼 카드 배경: `bg-card text-card-foreground` (다크에서도 분리감 있음)
  - 보조 텍스트: `text-muted-foreground`
  - 에러 inline: `text-destructive`
  - 포커스 링: ADR-010 기준 `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`

### 1.5 접근성 우선
- 모든 input에 `<Label>` 연결, 비밀번호 보기 토글은 `aria-pressed`.
- 에러는 `aria-live="polite"`로 스크린 리더 알림.
- Tab 순서는 시각 순서와 일치 — 4번 항목 참고.

---

## 2. 페이지 구조 와이어프레임

### 2.1 `/login` — 로그인

#### 데스크탑 (≥ md, 768px)

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  ┌────────────────────────┐  ┌──────────────────────────────┐   │
│  │  [Brand Mark]          │  │  로그인                       │   │
│  │  Moldy                 │  │  계정 정보를 입력해 주세요    │   │
│  │                        │  │                              │   │
│  │  AI 에이전트를          │  │  ┌────────────────────────┐ │   │
│  │  코드 한 줄 없이        │  │  │ 이메일                  │ │   │
│  │  만드는 가장            │  │  │ [you@example.com     ] │ │   │
│  │  단순한 방법.          │  │  └────────────────────────┘ │   │
│  │                        │  │                              │   │
│  │  · 노코드 빌더          │  │  ┌────────────────────────┐ │   │
│  │  · LangGraph 런타임     │  │  │ 비밀번호      [👁]      │ │   │
│  │  · 대화형 에이전트       │  │  │ [••••••••           ]  │ │   │
│  │                        │  │  └────────────────────────┘ │   │
│  │  (옵션 일러스트/패턴)    │  │                              │   │
│  │                        │  │  ☐ 로그인 유지   비번 찾기 → │   │
│  │                        │  │                              │   │
│  │                        │  │  ┌────────────────────────┐ │   │
│  │                        │  │  │      로그인             │ │   │
│  │                        │  │  └────────────────────────┘ │   │
│  │                        │  │  ─────  또는  ─────          │   │
│  │                        │  │  [🇬 Google로 로그인 (곧)]   │   │
│  │                        │  │                              │   │
│  │                        │  │  계정이 없으신가요? 회원가입 →│   │
│  │                        │  └──────────────────────────────┘   │
│  └────────────────────────┘                                      │
│   (좌측 1/2)                  (우측 폼, max-w-[420px] center)    │
└──────────────────────────────────────────────────────────────────┘
```

좌측 컬럼:
- 그리드: `lg:grid-cols-2` (작은 데스크탑은 단일 컬럼으로 fallback)
- 배경: `bg-muted/30` (또는 미세한 라이트 패턴 — placeholder)
- 콘텐츠: 중앙정렬 `flex flex-col justify-center px-12`
- 브랜드 마크 + tagline + 핵심 가치 3줄 bullet (i18n key: `auth.login.benefits.*`)

우측 컬럼:
- 폼: `Card` (border, `shadow-sm`, `rounded-2xl`, `p-8`, `max-w-[420px]`)
- 헤더: 제목 `text-2xl font-semibold tracking-tight` + 설명 `text-sm text-muted-foreground`

#### 모바일 (< md)

```
┌──────────────────────┐
│  Moldy               │  ← brand bar (sticky 아님, scroll 가능)
│  AI 에이전트 빌더     │
├──────────────────────┤
│                      │
│  로그인               │
│  계정 정보를 입력      │
│                      │
│  [이메일          ]   │
│                      │
│  [비밀번호  👁    ]   │
│                      │
│  ☐ 유지   비번 찾기   │
│                      │
│  [    로그인     ]    │
│                      │
│  ── 또는 ──           │
│  [ Google (곧) ]      │
│                      │
│  회원가입 →            │
│                      │
└──────────────────────┘
```
- 단일 컬럼, 좌우 padding `px-6`
- 폼 카드는 모바일에서 `border-0 shadow-none p-0` (여백만으로 분리)

#### 필드 명세

| 필드 | type | 검증 | placeholder |
|------|------|------|-------------|
| 이메일 | `email`, `autoComplete="email"`, `inputMode="email"`, `required` | 클라이언트는 단순 `@` 포함만 검사. 강한 검증은 서버 | `you@example.com` |
| 비밀번호 | `password`, `autoComplete="current-password"`, `required` | 빈 값 검사만 | (없음) |

#### 보조 요소
- **로그인 유지 체크박스** (`Checkbox` + `Label`): MVP에서는 **placeholder** — 항상 체크된 것처럼 동작 (refresh token 30일이 기본). UI는 보이지만 onChange는 no-op이고 `data-placeholder="true"`. Tooltip "현재는 항상 로그인이 유지됩니다."
- **"비밀번호 찾기"** 링크: Phase 2 placeholder. `<a>` 대신 `<button type="button">`로 두고 `onClick`은 toast.info("이메일 인증 후 지원될 예정입니다 — Phase 2"). `aria-disabled="true"`.
- **Google로 로그인** 버튼: `Button variant="outline"` + Google G 아이콘 + 텍스트 "Google로 로그인". `disabled`. `Tooltip`: "곧 지원될 예정입니다 (Phase 2)". 버튼 자체에 `cursor-not-allowed` opacity 60%.
  - 버튼 위 separator: `<div className="relative my-4"><div className="border-t border-border/60" /><span className="absolute inset-0 flex items-center justify-center"><span className="bg-card px-2 text-xs text-muted-foreground">또는</span></span></div>`

#### 하단 링크
```
계정이 없으신가요?  회원가입 →
```
- `text-sm text-muted-foreground text-center`
- "회원가입" 부분만 `text-primary-strong hover:underline`

### 2.2 `/register` — 회원가입

#### 레이아웃
- `/login`과 **동일한 2컬럼 구조** (좌측 인트로 동일, 우측 폼 카드)
- 좌측 카피만 변경: "지금 시작하세요" + 가입 후 첫 단계 안내 (i18n: `auth.register.intro.*`)

#### 필드 (위에서 아래 순서)

| 필드 | type | 검증 (클라이언트) | placeholder |
|------|------|------|-------------|
| 이름 | `text`, `autoComplete="name"`, `required`, `maxLength=80` | 1자 이상 | `홍길동` |
| 이메일 | `email`, `autoComplete="email"`, `required` | `@` 포함 | `you@example.com` |
| 비밀번호 | `password`, `autoComplete="new-password"`, `required` | **8자 이상** (서버와 동일) | (없음) |
| 비밀번호 확인 | `password`, `autoComplete="new-password"`, `required` | 위와 일치 | (없음) |

#### 비밀번호 강도 indicator

비밀번호 input 바로 아래 `mt-2`:
```
[━━━━━━─────────]  약함 / 보통 / 강함
```
- 막대: `flex h-1 gap-1` 4세그먼트(`flex-1 rounded-full`).
- 점수 규칙(단순 길이 기반, 추후 zxcvbn으로 교체 가능):
  - 0~7자: 0세그먼트 활성, 라벨 "비밀번호는 8자 이상이어야 합니다" `text-destructive`
  - 8~9자: 1세그먼트 (`bg-status-warn`), 라벨 "약함" `text-status-warn`
  - 10~13자: 2세그먼트 (`bg-status-warn`), 라벨 "보통"
  - 14~17자: 3세그먼트 (`bg-status-success`), 라벨 "강함"
  - 18자+ 또는 영숫자+기호 혼합: 4세그먼트 (`bg-status-success`), 라벨 "매우 강함"
- 비활성 세그먼트: `bg-muted`
- `aria-label="비밀번호 강도"`, `role="meter"`, `aria-valuenow={score}`, `aria-valuemin=0`, `aria-valuemax=4`.

비밀번호 확인 mismatch 시: `aria-invalid="true"` + helper text "비밀번호가 일치하지 않습니다" `text-destructive`.

#### 약관 동의 체크박스 (placeholder)
```
☐ 서비스 이용약관 및 개인정보처리방침에 동의합니다 (필수)
```
- MVP에서는 항상 true로 강제 (체크 해제 시 가입 버튼 disabled)
- 약관/정책 링크는 `<a>` placeholder ("준비 중")
- Phase 2에서 실제 약관 페이지 연결 — UI 자체는 변경 없음

#### 가입 버튼
- 텍스트: "가입하기"
- 비활성 조건: 모든 필수 필드 미충족 OR 비밀번호 mismatch OR 약관 미동의
- pending: `<Loader2 className="mr-2 size-4 animate-spin" />` + 텍스트 "가입 중...", 폼 전체 `pointer-events-none opacity-70` + input `disabled`

#### 하단 링크
```
이미 계정이 있으신가요?  로그인 →
```

---

## 3. UserMenu (사이드바 하단)

### 3.1 위치 및 구조

기존 `app-sidebar.tsx:365-410`의 "User Profile" 섹션을 **그대로 대체**한다 (구조는 유지, 데이터 소스만 `useSession()`으로 변경).

```
┌─ Sidebar 하단 ────────────────────────┐
│  ...메뉴들...                          │
│  ─────────────────────────────────────│
│  ┌──┐                              ▾  │
│  │JD│  John Doe         [관리자]      │  ← super_user일 때 배지
│  └──┘  john@example.com              │
└──────────────────────────────────────┘
```

### 3.2 아바타

- 컴포넌트: shadcn `Avatar` (`size-8 rounded-lg`) — 기존 `bg-sidebar-accent`와 통일
- 이미지: 사용자 `avatar_url` (MVP에는 없으므로 항상 fallback)
- Fallback: 이름의 **첫 두 글자**를 대문자로. 한국어 이름이면 첫 글자만 (e.g. "홍").
  ```tsx
  function initials(name: string): string {
    const parts = name.trim().split(/\s+/)
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
    return parts[0].slice(0, 2).toUpperCase()  // ASCII는 2글자, 한글은 첫 1글자만 잘림 = OK
  }
  ```
- Fallback 배경: `bg-primary/15 text-primary-strong` (브랜드 일관성)

### 3.3 텍스트 영역

- 이름: `truncate font-medium text-sm leading-tight`
- 이메일: `truncate text-xs text-muted-foreground leading-tight`
- 컨테이너: `grid flex-1 min-w-0` — `min-w-0`이 truncate의 핵심

### 3.4 super_user 배지

이름 옆에 작은 배지 (조건부):
```tsx
{user.is_super_user && (
  <span className="inline-flex h-4 items-center rounded-full bg-status-accent/15 px-1.5 text-[10px] font-medium uppercase tracking-wider text-status-accent">
    관리자
  </span>
)}
```
- ADR-010의 `--status-accent` 토큰 사용 (브랜드 primary와 색 충돌 회피)
- 영어: "ADMIN"

### 3.5 드롭다운 메뉴 (열린 상태)

```
┌─────────────────────────┐
│  ▸ 프로필 설정          │  ← /settings (Phase 2 활성화, MVP는 placeholder)
│  ▸ API 키 관리          │  ← /credentials (바로 이동)
├─────────────────────────┤
│  ▸ 로그아웃             │  ← destructive 색상
└─────────────────────────┘
```

shadcn `DropdownMenu` 사용 (이미 import됨). 항목:
1. **프로필 설정** — `<UserIcon />` + "프로필 설정"
   - MVP: `onClick={() => toast.info('프로필 설정은 곧 지원됩니다')}` (placeholder)
   - Phase 2에서 `/settings/profile`로 변경
2. **API 키 관리** — `<KeyIcon />` + "API 키 관리"
   - `onClick={() => router.push('/credentials')}`
3. (Separator)
4. **로그아웃** — `<LogOutIcon />` + "로그아웃"
   - `className="text-destructive focus:text-destructive focus:bg-destructive/10"`
   - `onClick={onLogout}` — `useAuth().logout()` mutation 호출

### 3.6 첫 가입 직후 super_user toast

가입 응답에서 `user.is_super_user === true`이면 redirect 직후 1회만:
```tsx
toast.success('🎉 Super User로 등록되었습니다', {
  description: '시스템 credential을 관리할 수 있습니다.',
  duration: 6000,
})
```
중복 방지: `sessionStorage.setItem('moldy.super_user_welcomed', '1')` 확인 후 표시.

---

## 4. 상태 처리 명세

### 4.1 로딩 상태

#### 폼 제출 중
- 버튼: `disabled` + 좌측에 `<Loader2 className="mr-2 size-4 animate-spin" aria-hidden />` + 텍스트 "로그인 중..." / "가입 중..."
- 폼 전체: `<fieldset disabled={isLoading} className="contents">` — input들 자동 disabled
- `aria-busy="true"`를 `<form>`에 부여

#### 세션 초기 로딩 (`useSession()` pending)
- `AuthGuard` wrapper:
  - 화면 전체 skeleton: `<div className="flex h-screen items-center justify-center"><Loader2 className="size-6 animate-spin text-muted-foreground" /></div>`
  - 또는 사이드바/헤더 영역만 `<Skeleton>` (UX 부드러움 우선)

### 4.2 에러 상태

#### 인라인 폼 에러 (필드 단위)
- 필드 아래 `text-xs text-destructive mt-1`
- 컨테이너에 `aria-invalid="true"` + `aria-describedby={errorId}`
- 입력 시 즉시 클리어 (live validation)

#### 폼 레벨 에러 (제출 후)
- `Alert variant="destructive"` 폼 카드 헤더 바로 아래 (필드 위)
- `role="alert"` + `aria-live="assertive"` (즉시 알림)
- 닫기 버튼 없음 (다음 제출 시 자동 클리어)

#### 에러 메시지 매핑

| HTTP | 시나리오 | 메시지 (한국어) | 표시 위치 |
|------|---------|----------------|----------|
| 401 | 로그인 실패 | "이메일 또는 비밀번호가 올바르지 않습니다" | 폼 레벨 Alert |
| 409 | 이메일 중복 (가입) | "이미 사용 중인 이메일입니다" | 이메일 필드 인라인 |
| 422 | 비밀번호 너무 짧음 | "비밀번호는 8자 이상이어야 합니다" | 비밀번호 필드 인라인 |
| 422 | 이름 빈 값 | "이름을 입력해 주세요" | 이름 필드 인라인 |
| 423 | 계정 잠김 | "계정이 일시적으로 잠겼습니다. 15분 후 다시 시도해 주세요." | 폼 레벨 Alert |
| 429 | Rate limit | "잠시 후 다시 시도해 주세요" | 폼 레벨 Alert |
| 5xx | 서버 에러 | "잠시 후 다시 시도해 주세요. 문제가 계속되면 관리자에게 문의해 주세요." | 폼 레벨 Alert |
| network | 연결 실패 | "네트워크 연결을 확인해 주세요" + [재시도] 버튼 | 폼 레벨 Alert |

422의 detail 매핑은 백엔드 응답의 `loc` (e.g. `["body","password"]`)을 보고 분기. 알 수 없는 필드면 폼 레벨 Alert로 fallback.

### 4.3 글로벌 401 처리 (세션 만료)

`apiFetch`가 401을 받고 `/refresh`도 실패한 경우:
1. **Toast 표시**: `toast.error('세션이 만료되었습니다', { description: '다시 로그인해 주세요.' })`
2. **TanStack Query 캐시 invalidate**: `queryClient.clear()`
3. **Redirect**: 현재 path를 `callbackUrl` 쿼리에 보존하고 `/login`으로 이동
   ```ts
   const callback = encodeURIComponent(window.location.pathname + window.location.search)
   router.push(`/login?callbackUrl=${callback}`)
   ```
4. **callbackUrl 검증** (login 페이지 onSuccess에서): `startsWith('/')` && `!startsWith('//')` (open redirect 방어)

이 처리는 `lib/api/client.ts`의 인터셉터에서 단일 진입점으로 — 컴포넌트마다 처리하지 않음.

### 4.4 빈 상태 / 첫 진입

- **`/login` 첫 진입**: 폼 깨끗한 상태 (autofocus on 이메일 input)
- **`/login?callbackUrl=...` (만료 후 진입)**: 폼 위에 Alert (info, `bg-status-info/10 text-status-info`) "로그인이 필요합니다"
- **이미 로그인 중인 사용자가 `/login` 직접 접근**: middleware가 `/`로 리다이렉트 (서버측)

### 4.5 네트워크 오류

- `Alert variant="destructive"` + 메시지 + `<Button variant="outline" size="sm" onClick={retry}>다시 시도</Button>`
- 재시도는 마지막 mutation을 그대로 재호출

---

## 5. Onboarding 플로우

### 5.1 첫 로그인 후 환영 모달

#### 트리거 조건
- 가입 직후 자동 로그인 → 대시보드(`/`) 진입 시 1회
- 또는 `sessionStorage.getItem('moldy.onboarding_dismissed') !== '1'` && `useSession().data.user.created_at`이 5분 이내
- `OnboardingDialog` 컴포넌트가 대시보드 root에 mount되어 자체 판단

#### 다이얼로그 (DialogShell 사용)

```
┌──────────────────────────────────────────────────┐
│  🎉  Moldy에 오신 것을 환영합니다              ✕  │
│      AI 에이전트를 만들 준비가 거의 끝났어요      │
├──────────────────────────────────────────────────┤
│                                                  │
│  AI 에이전트를 만들려면 LLM API 키를            │
│  등록해야 합니다.                                │
│                                                  │
│  ┌─────────────────────────────────────────┐    │
│  │  📋 다음 중 하나를 등록해 주세요:        │    │
│  │     · OpenAI API Key                    │    │
│  │     · Anthropic API Key                 │    │
│  │     · Google AI Studio API Key          │    │
│  └─────────────────────────────────────────┘    │
│                                                  │
│  키는 암호화되어 저장되며 본인만 볼 수 있습니다. │
│                                                  │
├──────────────────────────────────────────────────┤
│                       [나중에]   [지금 등록]     │
└──────────────────────────────────────────────────┘
```

- `DialogShell` size=`md` height=`auto`
- 헤더 icon slot: `<PartyPopperIcon />` (lucide) 또는 emoji `🎉` + `bg-status-accent/15 text-status-accent`
- 본문은 ADR-010의 `space-y-6` / `space-y-3` 리듬 준수
- 하이라이트 박스: `bg-muted/40 rounded-lg p-4 border border-border/60`
- 푸터:
  - "나중에" — `Button variant="ghost"` → `sessionStorage.setItem('moldy.onboarding_dismissed', '1')` + 닫기
  - "지금 등록" — `Button` (primary) → `router.push('/credentials')` + 닫기 + dismissed 플래그 set

### 5.2 첫 에이전트 생성 가드

`/agents/new` 또는 builder 진입 시:
1. `useSession()`으로 user 확인
2. `useQuery(['credentials','for-llm'])`로 LLM credential 보유 여부 체크
3. 없으면 → `/credentials?redirect=/agents/new` 로 redirect
4. `/credentials` 페이지 상단에 `Alert` (info):
   ```
   ⓘ  AI 에이전트를 만들려면 먼저 LLM API 키를 등록해 주세요.
       등록 후 자동으로 이전 화면으로 돌아갑니다.
       [← 돌아가기]
   ```
5. credential 등록 완료 시 mutation `onSuccess`에서 `redirect` 쿼리 파라미터로 자동 복귀

이 동작은 `useRequireLlmCredential()` 훅으로 감싸서 재사용 (signature: `() => { hasCredential: boolean, isLoading: boolean }`).

### 5.3 super_user 전용 표시 (시스템 credential)

`/credentials` 페이지에서:
- 일반 사용자: 본인 credential만 노출 (백엔드가 필터)
- super_user: 본인 + 시스템 — 시스템 credential은 명확히 구분
  - 카드 좌측 상단에 `Badge variant="outline"` "시스템" (`bg-status-accent/10 text-status-accent border-status-accent/30`)
  - 일반 카드와 약간의 시각적 구분 (배경 `bg-muted/30`)

이 부분은 본 스펙의 직접 범위 외이지만 onboarding과 연결되므로 명시.

---

## 6. 컴포넌트 목록

저커버그가 아래 표만 보고 한 줄도 추가 결정 없이 구현 가능하도록 작성했다.

| 컴포넌트 | 파일 경로 | 핵심 props | 의존 | 비고 |
|----------|-----------|-----------|------|------|
| `LoginForm` | `frontend/src/components/auth/LoginForm.tsx` | `onSubmit(email, password): Promise<void>`, `isLoading: boolean`, `error: AuthError \| null`, `defaultEmail?: string` | shadcn Form, Input, Button, Checkbox, Alert | 비밀번호 보기 토글 내장. callbackUrl 파라미터는 페이지 컴포넌트가 처리 |
| `RegisterForm` | `frontend/src/components/auth/RegisterForm.tsx` | `onSubmit({name, email, password}): Promise<void>`, `isLoading`, `error` | 위와 동일 + `Progress` 또는 자체 strength bar | 비밀번호 강도 indicator + 확인 필드 mismatch 검증 |
| `UserMenu` | `frontend/src/components/auth/UserMenu.tsx` | `user: { id, name, email, is_super_user }`, `onLogout: () => void` | shadcn Avatar, DropdownMenu | 사이드바 하단의 기존 블록 대체 |
| `AuthGuard` | `frontend/src/components/auth/AuthGuard.tsx` | `children: ReactNode`, `fallback?: ReactNode` | `useSession()` | session pending이면 fallback, error/없음이면 redirect /login |
| `OnboardingDialog` | `frontend/src/components/auth/OnboardingDialog.tsx` | `open: boolean`, `onClose: () => void`, `onPrimary: () => void` | `DialogShell` | 자체 trigger 로직은 dashboard root에 |
| `PasswordStrengthMeter` | `frontend/src/components/auth/PasswordStrengthMeter.tsx` | `password: string` | (없음) | 4세그먼트 막대 + 라벨, `role="meter"` |
| `SessionExpiredToast` | (없음 — `lib/api/client.ts`에서 `toast.error` 직접 호출) | — | sonner | 별도 컴포넌트 만들지 않음, 함수 호출만 |

### 6.1 페이지 컴포넌트

| 파일 | 책임 |
|------|------|
| `frontend/src/app/(auth)/layout.tsx` | 인증 전용 레이아웃 — 사이드바 없음, `<main>`에 2컬럼 그리드 |
| `frontend/src/app/(auth)/login/page.tsx` | `LoginForm` 호스팅, `useAuth().login` mutation, callbackUrl 처리 |
| `frontend/src/app/(auth)/register/page.tsx` | `RegisterForm` 호스팅, `useAuth().register` mutation, super_user welcome toast |

### 6.2 훅/유틸

| 파일 | export |
|------|--------|
| `frontend/src/lib/auth/session.ts` | `useSession()` (TanStack Query) |
| `frontend/src/lib/hooks/useAuth.ts` | `useAuth()` returning `{ login, register, logout, isPending }` |
| `frontend/src/lib/auth/csrf.ts` | `getCsrfToken()`, `setCsrfToken(token)`, `clearCsrfToken()` (in-memory + sessionStorage backup) |

### 6.3 진입점 — 저커버그가 가장 먼저 만질 파일

```
frontend/src/lib/api/client.ts   ← 여기부터 시작
```
이 파일에 `credentials: 'include'` + CSRF 헤더 + 401 자동 refresh + 만료 toast/redirect를 박으면, 나머지 컴포넌트는 그 위에 자연스럽게 얹어진다.

---

## 7. 접근성 (WCAG AA)

### 7.1 폼 레이블

- 모든 `<Input>`은 `<Label htmlFor={id}>`와 명시적 연결
- 비밀번호 보기 토글: `<button type="button" aria-pressed={visible} aria-label="비밀번호 표시/숨김">`
- 체크박스도 동일 — `<Label htmlFor>` 또는 wrapping `<Label>`로 클릭 영역 확장

### 7.2 Tab 순서

`/login` 데스크탑:
1. 이메일 입력
2. 비밀번호 입력
3. 비밀번호 보기 토글
4. "로그인 유지" 체크박스
5. "비밀번호 찾기" 링크 (Phase 2 — `aria-disabled`이지만 tab은 가능)
6. **"로그인" 버튼** (primary)
7. "Google로 로그인" 버튼 (disabled — tab skip)
8. "회원가입" 링크

`/register`:
1. 이름 → 2. 이메일 → 3. 비밀번호 → 4. 비밀번호 보기 토글 → 5. 비밀번호 확인 → 6. 약관 체크박스 → 7. "가입하기" 버튼 → 8. "로그인" 링크

UserMenu 드롭다운:
1. trigger 버튼 (Tab 도달 시 Enter/Space로 열림)
2. 첫 메뉴 아이템 (Arrow Down으로 이동)
3. ESC로 닫기

### 7.3 에러 알림

- 폼 레벨 Alert: `role="alert"` + `aria-live="assertive"` (즉시 안내)
- 인라인 helper text: `aria-live="polite"` + `id={`${field}-error`}` + 필드의 `aria-describedby`
- 비밀번호 강도: `role="meter" aria-valuenow aria-valuemin aria-valuemax aria-label`

### 7.4 포커스 가시성

- ADR-010의 표준 포커스 링: `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`
- 버튼만 추가: `focus-visible:ring-offset-2 focus-visible:ring-offset-background`
- 다이얼로그 내부 포커스 트랩: Radix DialogPrimitive 기본 동작 (DialogShell이 사용)

### 7.5 키보드 단축키

- Enter: 폼 제출 (다른 단축키 필요 없음 — 단순함 우선)
- Tab/Shift+Tab: 표준 이동
- ESC: 다이얼로그 닫기 (Onboarding) — Radix 기본
- 비밀번호 보기 토글: Space 또는 Enter

### 7.6 모션

- `prefers-reduced-motion` 존중 — Tailwind v4의 motion-safe/motion-reduce 변형 사용
- 다이얼로그 애니메이션은 ADR-010의 200ms zoom + fade

---

## 8. 디자인 토큰

**새 토큰을 만들지 않는다.** ADR-010 + shadcn 기본 토큰만 사용.

### 8.1 사용하는 토큰 인벤토리

| 토큰 | 용도 |
|------|------|
| `--background` / `--foreground` | 페이지 기본 |
| `--card` / `--card-foreground` | 폼 카드 표면 |
| `--popover` / `--popover-foreground` | 드롭다운, 다이얼로그 |
| `--primary` / `--primary-foreground` | 메인 CTA 버튼 |
| `--primary-strong` | 링크, 활성 텍스트, 아바타 fallback 텍스트 색 |
| `--secondary` / `--secondary-foreground` | 보조 버튼 (덜 사용) |
| `--muted` / `--muted-foreground` | 보조 텍스트, 배경 fill, 인트로 영역 |
| `--accent` / `--accent-foreground` | hover 상태 |
| `--destructive` / `--destructive-foreground` | 에러, 로그아웃 |
| `--border` | 카드/구분선 |
| `--input` | input border |
| `--ring` | 포커스 링 (ADR-010 알파 내장) |
| `--status-success` | 비밀번호 강도 "강함" 이상 |
| `--status-warn` | 비밀번호 강도 "약함/보통" |
| `--status-info` | 콜백 안내, info Alert |
| `--status-accent` | super_user 배지, onboarding 아이콘 |
| `--status-danger` | (= destructive 별칭) |

### 8.2 금지

- ❌ raw `bg-emerald-*`, `text-blue-*`, `bg-zinc-*` 등 Tailwind 팔레트 직접 사용
- ❌ 임의 hex (`#10b981`) 또는 oklch literal
- ❌ DialogShell 우회 (`Dialog` 직접 사용)
- ❌ `sm:max-w-2xl` 같은 임의 사이즈 — `DIALOG_SIZE` 토큰 사용

### 8.3 스페이싱 / 라운딩

- 카드 라운딩: `rounded-2xl` (DialogShell과 동일)
- 카드 그림자: `shadow-sm` (로그인/가입은 다이얼로그가 아닌 페이지이므로 강한 shadow 불필요)
- 폼 내부 간격: `space-y-4`
- 라벨↔입력: `space-y-1.5`
- 입력↔helper text: `mt-1`

### 8.4 타이포그래피

- 페이지 제목: `text-2xl font-semibold tracking-tight`
- 설명: `text-sm text-muted-foreground`
- 라벨: `text-sm font-medium` (shadcn Label 기본)
- 인라인 에러/helper: `text-xs`
- 배지: `text-[10px] uppercase tracking-wider`

---

## 부록 A. i18n 키 인벤토리 (한국어 1차)

```yaml
auth:
  login:
    title: "로그인"
    subtitle: "계정 정보를 입력해 주세요"
    email: "이메일"
    password: "비밀번호"
    rememberMe: "로그인 유지"
    forgotPassword: "비밀번호 찾기"
    submit: "로그인"
    submitting: "로그인 중..."
    googleButton: "Google로 로그인"
    googleComingSoon: "곧 지원될 예정입니다"
    or: "또는"
    noAccount: "계정이 없으신가요?"
    registerLink: "회원가입"
    benefits:
      title: "AI 에이전트를 코드 한 줄 없이"
      item1: "노코드 빌더"
      item2: "LangGraph 런타임"
      item3: "대화형 에이전트 생성"
    expiredNotice: "로그인이 필요합니다"
  register:
    title: "회원가입"
    subtitle: "Moldy 계정을 만들어보세요"
    name: "이름"
    email: "이메일"
    password: "비밀번호"
    passwordConfirm: "비밀번호 확인"
    terms: "서비스 이용약관 및 개인정보처리방침에 동의합니다"
    submit: "가입하기"
    submitting: "가입 중..."
    haveAccount: "이미 계정이 있으신가요?"
    loginLink: "로그인"
    strength:
      tooShort: "비밀번호는 8자 이상이어야 합니다"
      weak: "약함"
      medium: "보통"
      strong: "강함"
      veryStrong: "매우 강함"
    mismatch: "비밀번호가 일치하지 않습니다"
  errors:
    invalidCredentials: "이메일 또는 비밀번호가 올바르지 않습니다"
    emailTaken: "이미 사용 중인 이메일입니다"
    accountLocked: "계정이 일시적으로 잠겼습니다. 15분 후 다시 시도해 주세요."
    rateLimit: "잠시 후 다시 시도해 주세요"
    network: "네트워크 연결을 확인해 주세요"
    serverError: "잠시 후 다시 시도해 주세요. 문제가 계속되면 관리자에게 문의해 주세요."
    sessionExpired: "세션이 만료되었습니다"
    sessionExpiredDesc: "다시 로그인해 주세요."
  onboarding:
    title: "Moldy에 오신 것을 환영합니다"
    subtitle: "AI 에이전트를 만들 준비가 거의 끝났어요"
    body: "AI 에이전트를 만들려면 LLM API 키를 등록해야 합니다."
    providers: "다음 중 하나를 등록해 주세요"
    encryptedNote: "키는 암호화되어 저장되며 본인만 볼 수 있습니다."
    later: "나중에"
    register: "지금 등록"
    superUserToast: "🎉 Super User로 등록되었습니다"
    superUserToastDesc: "시스템 credential을 관리할 수 있습니다."
  userMenu:
    profile: "프로필 설정"
    credentials: "API 키 관리"
    logout: "로그아웃"
    adminBadge: "관리자"
    profileComingSoon: "프로필 설정은 곧 지원됩니다"
```

---

## 부록 B. 변경되지 않는 것 (Out of Scope)

본 스펙은 다음을 다루지 **않는다** — 명시적으로 향후 작업으로 미룸:

- 비밀번호 재설정 페이지 / 이메일 인증 페이지 (Phase 2)
- Google OAuth 콜백 페이지 (Phase 2)
- 프로필 편집 페이지 (Phase 2)
- 워크스페이스 스위처 (Future Phase)
- 사용자 검색 / 초대 (Future Phase)

이 모든 미래 화면들이 **현재 스펙과 일관**되도록 다음만 보장:
- `(auth)` route group은 인증 전용 레이아웃을 공유 — Phase 2 추가 시 동일 레이아웃 재사용
- UserMenu는 향후 항목 추가에 대비해 `DropdownMenu` 구조 유지 (Separator로 그룹 분리)
- 모든 새 페이지가 shadcn 토큰만 사용하면 자동으로 정렬됨

---

## 검증 체크리스트 (저커버그 구현 완료 시)

- [ ] `/login` 데스크탑/모바일 둘 다 시각 회귀 OK
- [ ] `/register` 비밀번호 강도 미터 4단계 동작
- [ ] 폼 레벨 Alert가 모든 에러 케이스에서 정확한 메시지 표시
- [ ] callbackUrl 파라미터 보존 (예: `/agents/123` → 로그인 → 자동 복귀)
- [ ] 401 → 자동 refresh → 실패 시 toast + redirect 동작
- [ ] UserMenu 드롭다운에 super_user면 "관리자" 배지 노출
- [ ] OnboardingDialog가 첫 가입 후 1회만 표시 (sessionStorage 체크)
- [ ] 다크모드에서 모든 화면 정상 (자동 — 토큰만 사용했으므로)
- [ ] 키보드만으로 로그인 → 가입 → 로그아웃 전 플로우 가능
- [ ] axe-core devtools 위반 0건 (또는 모두 known false-positive)
- [ ] `pnpm build` && `pnpm lint` 통과
