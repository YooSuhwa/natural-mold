<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## Tailwind cn() / twMerge 함정

`cn()`은 `twMerge(clsx(inputs))`다. **같은 그룹**(`p-*`, `gap-*`, `flex` vs `grid`, `rounded-*`)은 잘 머지된다. 하지만 **반응형 prefix는 별도 그룹**으로 취급되어 override되지 않는다.

```tsx
// base 클래스
'w-full max-w-[calc(100%-2rem)] sm:max-w-sm'

// override
cn(base, 'w-[720px]')
// 결과: w-[720px]는 적용되지만 sm:max-w-sm(384px)이 데스크톱에서 max-width를 384px로 clamp.
// 실제 너비는 384px로 강제됨.
```

**해결**: 반응형 reset을 명시
```tsx
cn(base, 'w-[720px] sm:max-w-none')
```

다른 함정 그룹: `dark:bg-*`, `hover:text-*`, `focus-visible:ring-*` 등 모든 modifier prefix.

## React 19 useEffect setState 안티패턴

ESLint 룰 `react-hooks/set-state-in-effect`는 prop 변경에 반응해 state를 리셋하는 패턴을 거부한다.

```tsx
// ❌ 거부됨
useEffect(() => {
  setConfirming(false)
}, [id])
```

**대안**:

1. **권장: 상위에서 remount** — Inner 컴포넌트로 분리 후 `key` 변경으로 자연스러운 unmount/remount.
   ```tsx
   export function MyDialog(props: Props) {
     return <MyDialogInner key={props.id ?? 'closed'} {...props} />
   }
   function MyDialogInner({ id, ... }: Props) {
     const [confirming, setConfirming] = useState(false)
     // id가 바뀌면 Inner가 재마운트되어 모든 state 자동 리셋
   }
   ```
   TanStack Query 캐시는 컴포넌트 라이프사이클과 무관하게 살아있으므로 데이터 fetch는 다시 일어나지 않는다.

2. derived state면 useState 대신 직접 계산.

이 패턴은 `components/{credential,skill,tool,mcp}/*-detail-dialog.tsx`와 `shared/base-detail-dialog.tsx`에서 사용 중.

## Playwright E2E 인증 세션 규칙

E2E 테스트에서 로그인 때문에 실패가 반복될 때, 각 spec/test가 로그인 폼을 직접
통과하게 만들지 않는다. 표준 방식은 **Playwright global setup에서 한 번 API
로그인 세션을 만들고 `storageState`로 모든 브라우저 컨텍스트에 주입**하는 것이다.

권장 구조:

- `e2e/global-setup.mjs`에서 테스트 시작 전에 `/api/auth/login` 호출
- E2E 전용 계정은 환경 변수로 받는다. 권장 이름은
  `E2E_USER_EMAIL`, `E2E_USER_PASSWORD`이며, 로컬 dev/test에서만 안전한
  기본값을 둘 수 있다. 운영/공유 staging 계정 비밀번호를 repo에 하드코딩하지 않는다
- 로그인 실패 시 같은 E2E 계정으로 `/api/auth/register` 시도; 이미 존재하면
  `409` 이후 다시 로그인
- 로그인 성공 후 `api.storageState({ path: './e2e/.auth/user.json' })`로
  HttpOnly auth cookie와 CSRF cookie가 포함된 세션 저장
- `playwright.config.ts`에서 `globalSetup`과
  `use.storageState: './e2e/.auth/user.json'`를 등록해 모든 페이지 테스트가
  로그인된 상태로 시작
- `PW_SKIP_BACKEND=1` mock-only 모드에서는 global setup이 가짜
  `moldy_rt`, `moldy_csrf` cookie를 저장하고, `e2e/fixtures.ts`가
  `/api/auth/me`를 E2E 유저로 mock
- `APIRequestContext`로 직접 생성/수정하는 smoke 테스트는 API 로그인 응답의
  `csrf_token`을 받아 mutation 요청에 `X-CSRF-Token` header로 넣는다

E2E auth failure를 고칠 때 먼저 확인할 것:

- global setup이 같은 E2E 계정으로 login → register fallback → login 순서를 타는지
- `playwright.config.ts`에 `globalSetup`과 `storageState`가 살아있는지
- `e2e/.auth/user.json`은 생성 산출물이므로 커밋하지 않는지
- 테스트가 로그인 페이지 UI에 의존하지 않고 `/` 진입 시 바로 dashboard/authenticated
  shell을 기대하는지
- mock-only 테스트가 `PW_SKIP_BACKEND=1`에서 `/api/auth/me`를 mock하고 있는지
- CSRF가 필요한 API 직접 호출에 `X-CSRF-Token`이 포함되어 있는지

### 병렬 실행 시 UI 단언 타임아웃

라이브 백엔드를 구동하는 UI 단언(`toBeVisible`/`toBeEnabled`)에 **기본 5초** 타임아웃을
그대로 쓰면 `--workers=4` + 스트리밍 spec 동시 실행에서 flaky하다. 원인은 checkpointer의
공유 PostgreSQL pool(루트 `CLAUDE.md` 참고)과 DB 부하가 백엔드를 직렬화시켜 무관한
목록 조회까지 느려지는 것이다.

- 백엔드 응답에 의존하는 단언은 **`{ timeout: 15_000 }`~`20_000`**으로 넉넉히 준다.
- API 검증은 `await expect.poll(async () => ..., { timeout: 15_000 })` 패턴을 따른다.
- flaky 판별: 격리 실행(`--workers=1`)에서 통과하면 인프라 부하 flake(허용),
  단독에서도 실패하면 실제 버그. origin/main 대조로 회귀 여부를 가른다.

## 디자인 토큰 + DialogShell

다이얼로그를 신설/마이그레이션할 때:
- 직접 `<DialogContent>`/`<Dialog>` 쓰지 말고 `<DialogShell>` 사용 (`components/shared/dialog-shell.tsx`)
- 사이즈는 `DIALOG_SIZE`/`DIALOG_HEIGHT` 토큰만 사용 (`lib/design-tokens.ts`). 임의값 `sm:max-w-2xl`/`max-h-[90vh]` 금지.
- 강조색은 `--primary`(채팅 사용자 메시지 배경) / `--primary-strong`(링크·탭 인디케이터). raw `bg-emerald-*` 금지 — Sprint 2 정리 대상.
- 시맨틱 상태색: `--status-{success,info,warn,danger,accent}`. raw `bg-amber-*`/`bg-sky-*` 금지.
- 상세 스펙: `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`

## Moldy 디자인 시스템 가드

제품 화면 코드에서 surface, radius, shadow, typography, focus 예외를 새로 만들지 않는다.
새 화면/컴포넌트 작업 후 아래 명령을 실행한다:

```bash
pnpm lint:design-system
```

가드가 막는 것:

- `rounded-xl/2xl/3xl` 직접 사용 — `moldy-card`, `moldy-panel`, `moldy-skeleton-card`, `moldy-muted-panel` 등 공용 surface class로 이동
- `shadow-sm/md/lg/xl/2xl` 및 `shadow-[...]` 직접 사용 — `moldy-popover`, `moldy-floating-icon-button`, `moldy-side-panel` 등 elevation class로 이동
- `bg-[#...]`, `text-[#...]`, `border-[#...]` raw hex utility — `--primary`, `--status-*`, `--moldy-*` semantic token으로 이동
- `bg-blue-500`, `text-emerald-700`, `border-rose-300` 같은 직접 Tailwind palette utility — `moldy-status-*`, `moldy-favorite-icon`, `moldy-data-type-*`, Agent Prism token 등 의미 class/token으로 이동
- `gap-[...]`, `p-[...]`, `m-[...]`, `size-[...]`, `w-[...]`, `h-[...]`, `grid-cols-[...]` 같은 임의 spacing/sizing utility — Tailwind scale token, `lib/design-tokens.ts`, 공용 layout API, 또는 `scripts/check-design-system.mjs`의 좁은 예외로 이동
- `z-[...]`, `z-40+`, `fixed inset-*`, `absolute inset-0`, `absolute ... z-*` overlay/stacking utility — shared overlay/positioning primitive 또는 `scripts/check-design-system.mjs`의 파일별 예외로 이동
- `text-[...]`, `leading-[...]`, `tracking-[...]`, `tracking-tight/tighter`, `outline-none`, `transition-all` — Moldy typography/focus/explicit transition 규칙 사용
- 제품 버튼/메뉴/탭/툴바의 inline `<svg>` — `lucide-react` 또는 Moldy-owned icon primitive로 이동
- `<Card>` 안의 `<Card>`, `moldy-card` 안의 `moldy-card`, 그리고 `<section>/<aside>`에 `moldy-card`/`moldy-panel`을 붙인 큰 surface — 현재는 `pnpm lint:design-system`의 warning baseline으로 보고된다. 새 화면을 추가할 때는 이 경고를 늘리기 전에 단일 surface + 내부 layout 또는 shared panel/tool primitive로 표현 가능한지 검토한다
- 임의 `style={...}` — 동적 layout/library API만 `scripts/check-design-system.mjs` allowlist에 이유와 함께 명시

현재 허용된 inline style 및 arbitrary layout 예외는
`scripts/check-design-system.mjs`에 파일별 이유와 함께 명시되어 있다. 대표 예외는
tree depth indentation, syntax highlighter theme, usage bar width, resource
grid columns, DialogShell size tokens, artifact preview panes, Agent Prism
trace/timeline layout, chat viewport clamps, phase progress ratio뿐이다. 직접
palette 색이 필요해 보여도 먼저 의미 class를 추가한다. overlay/stacking 예외도
chat right rail mobile layer, sticky transcript header, popover, resize handle,
Agent Prism marker처럼 실제 stacking 계약이 있는 경우만 허용한다. Typography
예외는 Agent Prism trace geometry처럼 렌더러/레이아웃 계약이 있는 경우만 허용한다.
inline SVG 예외는 data-driven chart와 브랜드/벤더 로고처럼 icon library 대체가
부적절한 경우만 허용한다. 예외를 늘릴 때는 먼저 공용 class/토큰으로 표현 가능한지
확인한다. 카드 구조 경고는 아직 실패 조건이 아니지만, 화면 구조 리팩토링 시
`pnpm lint:design-system` 출력의 `section-as-card`, `nested-card`,
`nested-moldy-card` 후보를 우선 정리 대상으로 본다.

## i18n 정적 텍스트 규칙

사용자에게 보이는 모든 정적 텍스트는 `next-intl` 메시지로 관리한다. TS/TSX에 한국어,
영어, placeholder, aria-label, title, toast 문구를 직접 하드코딩하지 않는다.

- 한국어가 source of truth다. 새 copy는 먼저 `messages/ko.json`에 자연스러운 한국어로
  추가하고, 같은 key path를 `messages/en.json`에 적절한 영어로 함께 추가한다.
- key path는 양쪽 파일에서 항상 정합되어야 한다. 한쪽만 추가하거나 이름이 어긋나면 안 된다.
- 컴포넌트에서는 `useTranslations()` 또는 서버 컴포넌트의 `getTranslations()`를 사용한다.
- UI copy를 추가/수정한 뒤에는 `pnpm lint:i18n`을 실행한다.
- `pnpm lint:i18n:strict`는 영어/ASCII 정적 텍스트까지 찾는 더 넓은 점검용이다. 현재
  일부 legacy Agent Prism 문구와 코드 조각 오탐이 남아 있으므로, 새 코드에서 걸리면
  i18n으로 옮기고 기존 오탐은 가드를 좁게 조정한다.

## Resource Card 문법

도구/스킬/MCP/자격증명/마켓플레이스/템플릿 같은 리소스 목록 카드는
`components/shared/resource-layout.tsx`의 `ResourceListCard`를 사용한다.

- 순서: `Header(icon + type badge)` → `Title` → `Subhead` → `Description` →
  `StatusRow` → `MetaRow` → `Footer`
- density: `compact`(선택 후보), `standard`(일반 관리 리소스), `rich`(상태/메타가 많은 운영 리소스)
- category/tone 색은 카드 전체 배경이나 장식 rail로 쓰지 않는다. neutral surface 위에
  icon, dot, status, hover/focus border처럼 의미가 있는 위치에서만 표현한다.
- 단일 액션 카드는 전체 `<button>`/`<Link>`가 가능하다.
- 보조 액션이 하나라도 있으면 root는 non-interactive `<article>`이고 footer에 명시적
  `<Button>`/`<Link>`를 둔다. `div role="button"`과 inline `window.location.href`
  navigation은 새 resource card에서 금지한다.

## Frontend folder architecture

- `app/**/page.tsx` should be a Server Component wrapper unless the entire route genuinely requires browser APIs at the page boundary.
- Route-only UI lives under that route's `_components/`, `_hooks/`, or `_lib/`.
- Components used by multiple routes in one product domain live under `src/features/<domain>/`.
- Components used across unrelated domains live under `src/components/shared/`.
- `src/components/ui/` is for shadcn/base primitives only and must not import app/domain hooks.
- Do not add new barrel exports for frontend modules unless the importing ergonomics clearly outweigh bundle/dev-time costs.

## Frontend commonization rules

- Resource list pages use `ResourcePage`, `ResourcePanel`, `ResourceGrid`, `ResourceListCard`, `SearchFilterBar`, and `ResourceListState` before creating route-specific shells.
- Settings pages use `SettingsShell`, `SettingsSectionCard`, and `FormFieldShell` before creating local card/field wrappers.
- New counted tabs use `CountedTabs` or existing `LineTabs`; do not hand-roll `role="tablist"` buttons.
- New dialogs use `DialogShell`; direct `DialogContent` is limited to `components/ui/dialog.tsx`, `components/shared/dialog-shell.tsx`, and shared confirmation primitives.
- CRUD tables may use `DataTable`; domain-specific expandable tables can stay local. Do not force metric/read-only tables into `DataTable` unless behavior is reused.

## Frontend preflight and performance rules

- Run `pnpm preflight` before build/dev diagnostics. The project expects Node 22 and installed frontend dependencies.
- After frontend refactors run `pnpm lint`, `pnpm lint:i18n`, `pnpm lint:design-system`, and `pnpm lint:frontend-architecture`.
- Do not add a new page-level `'use client'` without explaining why a Server Component wrapper plus Client island is insufficient.
- Keep heavy artifact viewers, markdown highlighters, document parsers, Mermaid, HWP/DOCX/XLSX/PPTX viewers, and similar libraries behind lazy/dynamic imports.
- New TanStack Query keys should be created through feature key factories, not ad hoc raw arrays inside components.
- Product date/time, number, USD, compact count, and file-size display formatting should use `src/lib/utils/display-format.ts`. Do not call `toLocaleString`, `toLocaleDateString`, `toLocaleTimeString`, or `new Intl.*Format` directly in `src/app` or `src/components`.
