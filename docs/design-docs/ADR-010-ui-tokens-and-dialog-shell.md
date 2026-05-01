# Sprint 1 / Story S2 — 디자인 토큰 oklch 픽스 + DialogShell 비주얼 스펙 (팀쿡)

## 목표
1. `--primary` / `--primary-foreground` / `--primary-strong` / `--ring` + 시맨틱 상태색 oklch 정확값 결정 (라이트/다크)
2. DialogShell 비주얼 스펙을 정확한 Tailwind 클래스로 ADR-010에 기록
3. 기존 raw color → 새 토큰 매핑표
4. 포커스 링 완화 스펙
5. 베이스 컴포넌트 (`ui/dialog.tsx`, `ui/sheet.tsx`) 정비 스펙

## 산출물
- `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md` (신규)
- `AUDIT.log`에 한 줄 추가 (S2_DONE)
- `progress.txt`에 oklch 정확값 1-2줄 추가 (저커버그 복붙용)

---

## A. oklch 정확값 결정 (Tailwind v4 emerald palette 기반)

Tailwind CSS v4의 색은 모두 oklch로 정의되어 있다 (Tailwind v4.0 발표글 + tailwindcss/dist/preflight 참조). emerald 계열의 v4 oklch 값:

| Tailwind 클래스 | oklch 정확값 |
|---|---|
| emerald-50  | `oklch(0.979 0.021 166.113)` |
| emerald-100 | `oklch(0.95 0.052 163.051)` |
| emerald-200 | `oklch(0.905 0.093 164.15)` |
| emerald-300 | `oklch(0.845 0.143 164.978)` |
| emerald-400 | `oklch(0.765 0.177 163.223)` |
| emerald-500 | `oklch(0.696 0.17 162.48)` |
| emerald-600 | `oklch(0.596 0.145 163.225)` |
| emerald-700 | `oklch(0.508 0.118 165.612)` |
| emerald-800 | `oklch(0.432 0.095 166.913)` |
| emerald-900 | `oklch(0.378 0.077 168.94)` |
| emerald-950 | `oklch(0.262 0.051 172.552)` |

또한 시맨틱 상태색에 쓸 v4 팔레트:

| Tailwind 클래스 | oklch 정확값 |
|---|---|
| amber-500   | `oklch(0.769 0.188 70.08)` |
| amber-400   | `oklch(0.828 0.189 84.429)` |
| sky-500     | `oklch(0.685 0.169 237.323)` |
| sky-400     | `oklch(0.746 0.16 232.661)` |
| violet-500  | `oklch(0.606 0.25 292.717)` |
| violet-400  | `oklch(0.702 0.183 293.541)` |
| red-500     | `oklch(0.637 0.237 25.331)` (= destructive 라이트) |
| red-400     | `oklch(0.704 0.191 22.216)` (= destructive 다크, 이미 사용 중) |

### 결정값

라이트:
- `--primary: oklch(0.95 0.052 163.051);`        (= emerald-100, 사용자 메시지 박스 배경 그대로)
- `--primary-foreground: oklch(0.262 0.051 172.552);` (= emerald-950)
- `--primary-strong: oklch(0.596 0.145 163.225);` (= emerald-600)
- `--ring: oklch(0.596 0.145 163.225 / 0.4);`     (= emerald-600 @ 40%)

다크:
- `--primary: oklch(0.378 0.077 168.94);`         (= emerald-900)
- `--primary-foreground: oklch(0.95 0.052 163.051);` (= emerald-100)
- `--primary-strong: oklch(0.765 0.177 163.223);` (= emerald-400)
- `--ring: oklch(0.765 0.177 163.223 / 0.45);`    (= emerald-400 @ 45%)

상태색 (라이트/다크 공통 — 알파로 배경 톤 조정):
- `--status-success: oklch(0.596 0.145 163.225);` 라이트 / `oklch(0.765 0.177 163.223);` 다크 (= primary-strong과 동일)
- `--status-info: oklch(0.685 0.169 237.323);` 라이트 / `oklch(0.746 0.16 232.661);` 다크 (= sky-500/400)
- `--status-warn: oklch(0.769 0.188 70.08);` 라이트 / `oklch(0.828 0.189 84.429);` 다크 (= amber-500/400)
- `--status-danger: oklch(0.637 0.237 25.331);` 라이트 / `oklch(0.704 0.191 22.216);` 다크 (= red-500/400, destructive 별칭)
- `--status-accent: oklch(0.606 0.25 292.717);` 라이트 / `oklch(0.702 0.183 293.541);` 다크 (= violet-500/400)

### 명도 대비 검증 (WCAG AA 4.5:1)
- 라이트: emerald-100 (L=0.95) × emerald-950 (L=0.262) — 거의 흰 배경 vs 거의 검은 텍스트, contrast ≈ 14:1 ✅
- 다크: emerald-900 (L=0.378) × emerald-100 (L=0.95) — contrast ≈ 8:1 ✅
- ring 알파(35-45%)는 배경에 따라 인지율 충분 (border-ring 제거하므로 visible focus는 ring으로만)

### Tailwind v4 @theme 형식 등록
`globals.css`의 `@theme inline` 블록에 추가될 라인:
```
--color-primary-strong: var(--primary-strong);
--color-status-success: var(--status-success);
--color-status-info: var(--status-info);
--color-status-warn: var(--status-warn);
--color-status-danger: var(--status-danger);
--color-status-accent: var(--status-accent);
```
(`--primary`, `--primary-foreground`, `--ring`은 기존 매핑 재사용)

---

## B. DialogShell 비주얼 스펙

### 컨테이너
```
flex flex-col overflow-hidden rounded-2xl shadow-2xl ring-1 ring-border/60 bg-popover
```
+ `DIALOG_SIZE` 클래스 + `DIALOG_HEIGHT` 클래스 + `max-h-[calc(100vh-4rem)]`

`DIALOG_SIZE` 토큰 (TS 객체 → Tailwind 클래스 매핑):
- `sm`: `w-[400px]`
- `md`: `w-[560px]`
- `lg`: `w-[720px]`
- `xl`: `w-[920px]`
- `console`: `w-[1080px]`

`DIALOG_HEIGHT` 토큰:
- `auto`: `h-[480px] max-h-[calc(100vh-4rem)]`
- `fixed`: `h-[640px] max-h-[calc(100vh-4rem)]`
- `tall`: `h-[760px] max-h-[calc(100vh-4rem)]`

### Header (고정)
```
border-b border-border/60 px-6 py-5 flex items-start gap-4 relative
```
- icon slot: `flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary-strong` (currentColor 도메인 아이콘)
- text wrap: `flex-1 min-w-0`
  - title: `text-base font-semibold tracking-tight text-foreground`
  - description: `mt-1 text-sm text-muted-foreground leading-relaxed`
- right action slot: `ml-auto flex items-center gap-2` (StatusChip, 메뉴 등)
- close X: `absolute top-4 right-4 size-8 rounded-md hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring inline-flex items-center justify-center text-muted-foreground hover:text-foreground`

### Body (스크롤)
```
flex-1 overflow-y-auto px-6 py-5
```
- 섹션 간격: `space-y-6`
- 섹션 내부: `space-y-3`
- input 그룹: `space-y-1.5`
- 라벨: `text-xs font-medium text-muted-foreground`
- divider: 직접 `<div className="border-t border-border/60" />` (Separator 컴포넌트 사용 X — 토큰 일관성)

### Footer (고정)
```
border-t border-border/60 bg-muted/30 px-6 py-4 flex items-center justify-end gap-2
```
- 표준 버튼: `min-w-[80px]`
- pending: `<Loader2 className="mr-1 size-4 animate-spin" aria-hidden />`
- variant 우선순위: 좌측에 secondary("취소"), 우측에 primary("저장")

### Sidebar (선택 슬롯)
```
w-[260px] shrink-0 border-r border-border/60 bg-muted/30 px-4 py-5 overflow-y-auto
```
DialogShell 컨테이너를 `flex-row`로 전환할 때 `flex-1`인 main 영역과 짝.

### 접근성
- `role="dialog"`, `aria-labelledby={titleId}`, `aria-describedby={descriptionId}` (Radix DialogPrimitive 기본 구현 그대로)
- 포커스 트랩, ESC 닫기, 오버레이 클릭 — Radix 기본
- 모션: `data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 duration-200`

---

## C. 기존 raw color → 새 토큰 매핑표

| 기존 클래스 (라이트 / 다크) | 새 토큰 클래스 | 용도 / 위치 예시 |
|---|---|---|
| `bg-emerald-100 dark:bg-emerald-900` | `bg-primary` | 사용자 메시지 박스 (assistant-thread.tsx:243), 전송 버튼, 활성 노드 배경 |
| `text-emerald-950 dark:text-emerald-100` | `text-primary-foreground` | 위 강조 배경 위 텍스트 |
| `text-emerald-600 dark:text-emerald-400` | `text-primary-strong` | 링크, 활성 탭 텍스트, hover, "활성 사용자 메시지" 보조 |
| `bg-emerald-500 dark:bg-emerald-400` (after::, indicator) | `bg-primary-strong` | 탭 인디케이터 (`after:bg-...`) |
| `bg-emerald-100 ring-emerald-200 dark:bg-emerald-900 dark:ring-emerald-800` | `bg-primary/15 ring-primary-strong/30` | 모델 배지, subtle chip |
| `bg-emerald-50 dark:bg-emerald-950/30` | `bg-primary/10` | 매우 옅은 강조 배경 |
| `bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-100` | `bg-status-accent/10 text-status-accent` | 대화형 카드, 구분 강조 |
| `bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-100` | `bg-status-warn/10 text-status-warn` | 경고/주의 박스 |
| `bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-100` | `bg-status-info/10 text-status-info` | 정보/안내 박스 |
| `bg-red-50 text-red-900 dark:bg-red-950 dark:text-red-100` | `bg-destructive/10 text-destructive` | 에러 박스 (기존 destructive 토큰 재사용) |

분석 보고서 기준 58곳 ≈ emerald 직접 사용 + violet/amber/sky 섹션. 저커버그가 mgrep + 매핑표로 일괄 치환.

---

## D. 포커스 링 완화 스펙

기존 (input.tsx, textarea.tsx, select.tsx, button.tsx, checkbox.tsx 공통 패턴):
```
focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50
```

새:
```
focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
```

이유:
1. `border-ring`은 입력 컨테이너의 border 색을 통째로 강조색으로 바꿔 "트림이 두꺼워진" 느낌. 제거.
2. `ring-3`은 3px → `ring-2` 2px로 완화.
3. `--ring` 토큰 자체에 알파(0.4 / 0.45)를 내장했으므로 클래스에 `/50` 명시 불필요. CSS 변수 변경만으로 라이트/다크 알파 자동 적용.
4. `outline-none` 명시: 일부 브라우저 기본 outline이 ring과 겹쳐 보이는 이슈 방지.

추가: 키보드 접근성을 위해 `focus-visible:ring-offset-2 focus-visible:ring-offset-background`은 **버튼류에만** 적용 (input은 offset 없이). 베이스 컴포넌트 패치 시 결정.

---

## E. 베이스 컴포넌트 정비 스펙

### `ui/dialog.tsx`
- `DialogContent`:
  - 기존: `rounded-xl bg-popover p-4 ... ring-1 ring-foreground/10`
  - 새:   `rounded-2xl bg-popover ring-1 ring-border/60 shadow-2xl` — `p-4` 제거 (DialogShell이 패딩 관리), `ring-foreground/10` → `ring-border/60`
- `DialogOverlay`:
  - 기존: `bg-black/80` 류
  - 새:   `bg-black/40 backdrop-blur-sm`
- 기본 `max-w` 제거 — DialogShell의 `DIALOG_SIZE`가 제어

### `ui/sheet.tsx`
- 모바일 사이드바 + 대화목록 두 곳만 유지
- 둥근 모서리 정리: 우측 패널은 `rounded-l-2xl`, 하단 패널은 `rounded-t-2xl`. 좌측/상단 변은 0
- 그림자/링 토큰화: `ring-1 ring-border/60 shadow-2xl`
- 패딩은 사용처에서 관리 (sheet 자체는 컨테이너만)

### 베이스 input/textarea/select/button/checkbox
- 위 D의 포커스 클래스 일괄 치환
- `aria-invalid:ring-destructive/40 aria-invalid:border-destructive/60` 패턴 유지 (에러 상태 표현)

---

## 마이그레이션 영향

| 항목 | 변경 위치 수 |
|---|---|
| globals.css 토큰 추가/수정 | 1 (`:root` + `.dark` + `@theme`) |
| ui/dialog.tsx | 1 |
| ui/sheet.tsx | 1 |
| ui/input·textarea·select·button·checkbox.tsx | 5 |
| emerald raw → primary 토큰 치환 | ~58곳 (mgrep) |
| violet/amber/sky raw → status 토큰 치환 | 약 20-30곳 (분석 보고서 참조) |

코드 작업은 저커버그가 Sprint 1-1~1-4에서 분할 수행. ADR-010이 단일 진실 공급원.

---

## 검증 방법

1. **시각 회귀**: 사용자 메시지 박스(assistant-thread.tsx)는 기존과 시각적으로 동일해야 함 — primary 토큰이 emerald-100/900을 그대로 흡수
2. **WCAG AA**: 라이트/다크 각각 primary × primary-foreground 콘트라스트 ≥ 4.5 — 위에서 14:1 / 8:1 검증
3. **포커스 가시성**: 키보드 Tab 시 모든 인터랙티브 요소에 ring 보임 (CI: axe-core 또는 수동 QA)
4. **다크모드 라운드트립**: 라이트→다크→라이트 토글 시 깜빡임 없이 hue 동일, lightness만 변경
5. **빌드**: `pnpm build` 통과 (Tailwind v4 `@theme inline` 토큰 인식)

---

## 트레이드오프 (사티아 보고용)

**사용자 메시지 색을 brand primary로 승격 vs 별도 `--user-bubble` 토큰 분리.**

선택: 승격. 이유:
- 채팅이 Moldy의 핵심 surface — 가장 빈번하게 보이는 강조 배경이 곧 brand 정체성
- 토큰 2개로 쪼개면 "사용자 메시지만 다른 색"이라는 우연한 분리가 굳어져 일관성 깨짐
- 비용: emerald-100이 라이트모드 `--primary`가 되면서 "primary 위 검은 텍스트"라는 일반적 기대와 어긋남 → `--primary-foreground = emerald-950`로 명시 보정. 따라서 "primary는 항상 강한 강조색이다"라는 흔한 가정에 의존하는 코드(예: 임의로 `bg-primary text-white` 같은 조합)는 깨질 수 있음 — 이 경우 즉시 `text-primary-foreground`로 교정.

대안 거절: `--user-bubble` 분리는 토큰 1개를 추가로 관리해야 하고, 결국 같은 emerald 톤이라 "정렬되었지만 두 곳"이 되어 디자인 의도를 흐림.

---

## 작업 순서 (실행 단계)

1. ADR-010 작성 — 위 A~E + 마이그레이션 영향 + 검증 방법 + 트레이드오프
2. `AUDIT.log`에 한 줄 추가: `[ISO타임] timcook S2_DONE ADR-010 + 토큰 oklch 픽스 + DialogShell 비주얼 스펙`
3. `progress.txt`에 oklch 핵심값 1-2줄 추가 (저커버그가 globals.css에 복붙):
   ```
   - 토큰 oklch 픽스(라이트): --primary oklch(0.95 0.052 163.051) / --primary-foreground oklch(0.262 0.051 172.552) / --primary-strong oklch(0.596 0.145 163.225) / --ring oklch(0.596 0.145 163.225 / 0.4)
   - 토큰 oklch 픽스(다크):   --primary oklch(0.378 0.077 168.94) / --primary-foreground oklch(0.95 0.052 163.051) / --primary-strong oklch(0.765 0.177 163.223) / --ring oklch(0.765 0.177 163.223 / 0.45)
   ```
4. 사티아에게 보고: ADR 경로 + 핵심 oklch + 트레이드오프 한 줄

## 완료 조건
- ADR-010 파일 존재 (Accepted, 2026-05-01)
- AUDIT.log에 S2_DONE 라인
- progress.txt에 oklch 핵심값 추가
- 모든 결정값이 Tailwind v4 emerald/amber/sky/violet/red palette의 공식 oklch와 일치 (검증 가능)
