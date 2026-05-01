# Sprint 1 / Story S3 — 디자인 토큰 + 베이스 정비 + DialogShell + 보조 컴포넌트

**역할**: 저커버그 (TTH 프론트엔드 사일로)
**작업 디렉토리**: `/Users/chester/dev/ref/natural-mold/frontend/`
**참조**: ADR-010, sheet-deletion-analysis.md, progress.txt 2026-05-01 섹션

---

## 사전 확인 (read-only)

- [x] ADR-010 정독 — oklch 정확값, DialogShell 스펙, 포커스 링 완화 스펙 확인
- [x] globals.css 현재 상태 확인 — `@theme inline` 블록은 Tailwind v4 형식, `:root` + `.dark`에 토큰 정의
- [x] components/shared/ 디렉토리 — 9개 기존 파일 (page-header.tsx 등 보존 필수)
- [x] lib/ 디렉토리 — `constants/` 미존재 → 신규 생성 필요
- [x] AGENTS.md — Next.js 16 주의: docs 폴더 참조 (이번 작업은 SC 변경 없으므로 영향 적음)

**탐색 자제**: 27개 DialogContent 사용처 / 9개 페이지 / 4개 detail-sheet는 본 스토리 범위 외. 만지지 않음.

---

## 단계별 작업 + 검증

### Phase 1 — Constants & design tokens (의존성 0)

**Files (신규)**:
1. `src/lib/design-tokens.ts` — `DIALOG_SIZE` (sm/md/lg/xl/console), `DIALOG_HEIGHT` (auto/fixed/tall) + 타입
2. `src/lib/constants/model.ts` — `MODEL_DEFAULTS` (temperature/topP/maxTokens)
3. `src/lib/constants/timing.ts` — `COPY_FEEDBACK_MS`, `WITTY_LOADING_ROTATE_MS`, `HEALTH_POLL_INTERVAL_MS`
4. `src/lib/constants/usage.ts` — `USAGE_PRESETS` + 타입

이들은 프로젝트의 어떤 파일에서도 import되지 않으므로 빌드 영향 없음.

### Phase 2 — globals.css 토큰 갱신 (파괴적 변경)

`src/app/globals.css` 편집:

**A. `:root` 블록 (라이트)**
- `--primary: oklch(0.205 0 0);` → `oklch(0.95 0.052 163.051);` (emerald-100)
- `--primary-foreground: oklch(0.985 0 0);` → `oklch(0.262 0.051 172.552);` (emerald-950)
- 신설: `--primary-strong: oklch(0.596 0.145 163.225);` (emerald-600)
- `--ring: oklch(0.708 0 0);` → `oklch(0.596 0.145 163.225 / 0.4);` (emerald-600 @ 40%)
- 신설 시맨틱 상태색:
  - `--status-success: oklch(0.596 0.145 163.225);`
  - `--status-info: oklch(0.685 0.169 237.323);` (sky-500)
  - `--status-warn: oklch(0.769 0.188 70.08);` (amber-500)
  - `--status-danger: oklch(0.637 0.237 25.331);` (red-500)
  - `--status-accent: oklch(0.606 0.25 292.717);` (violet-500)

**B. `.dark` 블록**
- `--primary: oklch(0.922 0 0);` → `oklch(0.378 0.077 168.94);` (emerald-900)
- `--primary-foreground: oklch(0.205 0 0);` → `oklch(0.95 0.052 163.051);` (emerald-100)
- 신설: `--primary-strong: oklch(0.765 0.177 163.223);` (emerald-400)
- `--ring: oklch(0.556 0 0);` → `oklch(0.765 0.177 163.223 / 0.45);`
- 신설 시맨틱 상태색 (다크):
  - `--status-success: oklch(0.765 0.177 163.223);`
  - `--status-info: oklch(0.746 0.16 232.661);` (sky-400)
  - `--status-warn: oklch(0.828 0.189 84.429);` (amber-400)
  - `--status-danger: oklch(0.704 0.191 22.216);` (red-400)
  - `--status-accent: oklch(0.702 0.183 293.541);` (violet-400)

**C. `@theme inline` 블록**
기존 매핑 보존하면서 추가:
```
--color-primary-strong: var(--primary-strong);
--color-status-success: var(--status-success);
--color-status-info: var(--status-info);
--color-status-warn: var(--status-warn);
--color-status-danger: var(--status-danger);
--color-status-accent: var(--status-accent);
```

**검증**: `pnpm build`. 토큰 추가만 했으니 통과 예상. `--primary` 변경은 기존 사용처에 시각 영향 있지만 빌드는 통과해야 함.

**파괴적 변경 노트**: `bg-primary text-white` 같은 가정이 깨지는 사용처는 본 스토리에서 건드리지 않고 progress.txt에 메모. Sprint 2(S4/S5)에서 일괄 처리.

### Phase 3 — UI 베이스 컴포넌트 정비

**5개 파일 포커스 링 일괄 치환**:
- `src/components/ui/input.tsx`
- `src/components/ui/textarea.tsx`
- `src/components/ui/select.tsx`
- `src/components/ui/button.tsx`
- `src/components/ui/checkbox.tsx`

기존 패턴: `focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50`
새 패턴: `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`

각 파일을 먼저 Read한 후 정확한 클래스 문자열을 Edit. `aria-invalid:` 류는 보존.

**`src/components/ui/dialog.tsx`**:
- DialogContent: `rounded-xl` → `rounded-2xl`, `ring-1 ring-foreground/10` → `ring-1 ring-border/60`, `shadow-2xl` 추가 (없으면), `p-4` 유지 (호환성)
- DialogOverlay: `bg-black/80` → `bg-black/40 backdrop-blur-sm`
- 닫기 X 버튼이 정의되어 있다면 `hover:bg-muted/60 rounded-md size-8` 보강

**`src/components/ui/sheet.tsx`**:
- 우측 패널 `rounded-l-2xl`, 하단 `rounded-t-2xl` (있으면 톤 일관성)
- 큰 변경 X

**검증**: `pnpm build` 통과.

### Phase 4 — Shared 컴포넌트 (6개 신규)

순서 (의존성 순):
1. `src/components/shared/error-state.tsx` — 의존: ui/button. `text-status-danger` 토큰 검증 (없으면 `text-destructive` fallback + 메모)
2. `src/components/shared/dialog-shell.tsx` — 의존: ui/dialog, lib/design-tokens, lib/utils. shadcn dialog의 export 시그니처 확인 후 작성
3. `src/components/shared/delete-confirm-inline.tsx` — 의존: ui/button, lucide-react
4. `src/components/shared/form-footer.tsx` — 의존: ui/button, lucide-react
5. `src/components/shared/page-shell.tsx` — 의존: shared/page-header (기존), shared/error-state (신규)
6. `src/components/shared/base-detail-dialog.tsx` — 의존: dialog-shell, delete-confirm-inline, error-state, ui/skeleton, ui/button, @tanstack/react-query, useEffect로 id 변경 시 confirming 리셋 추가

**검증**: `pnpm build` 통과 (사용처 없으니 import 에러만 잡으면 OK).

### Phase 5 — 최종 검증
- `pnpm build` 최종 통과
- `pnpm lint` errors 0 (warnings 무시)

---

## 실패 시 대응

- 1회 실패: 에러 메시지 분석 후 즉시 수정
- 2회 실패: 다른 접근 (예: 토큰 키명 충돌이면 키명 조정, shadcn export 시그니처 차이면 import 경로 변경)
- 3회 실패: 사티아에게 에스컬레이션 (어떤 시도/추측 원인 명시)

---

## 완료 보고 (사티아)

1. AUDIT.log에 한 줄 추가:
   `[ISO시간] zuckerberg S3_DONE 토큰+베이스 정비+DialogShell 6개 신규 — pnpm build PASS`
2. progress.txt에 패턴 1-3줄:
   - shadcn dialog/sheet 실제 export 시그니처
   - globals.css `@theme inline` 매핑 시 주의점 (있으면)
   - 파괴적 변경(`--primary`) 사이드 이펙트 (있으면 위치)
3. 짧은 보고: 빌드 결과 / 시각 깨진 곳 / S4 진입에 필요한 정보

---

## 범위 제한 (절대 금지)

- 4개 detail-sheet 변환 X (S4)
- 27개 DialogContent 사용처 수정 X (S5)
- 9개 페이지 PageShell 도입 X (S5)
- emerald raw 색상 일괄 치환 X (Sprint 2)
- agents settings RHF+Zod 변환 X (Sprint 3)
