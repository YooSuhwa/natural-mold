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

## 디자인 토큰 + DialogShell

다이얼로그를 신설/마이그레이션할 때:
- 직접 `<DialogContent>`/`<Dialog>` 쓰지 말고 `<DialogShell>` 사용 (`components/shared/dialog-shell.tsx`)
- 사이즈는 `DIALOG_SIZE`/`DIALOG_HEIGHT` 토큰만 사용 (`lib/design-tokens.ts`). 임의값 `sm:max-w-2xl`/`max-h-[90vh]` 금지.
- 강조색은 `--primary`(채팅 사용자 메시지 배경) / `--primary-strong`(링크·탭 인디케이터). raw `bg-emerald-*` 금지 — Sprint 2 정리 대상.
- 시맨틱 상태색: `--status-{success,info,warn,danger,accent}`. raw `bg-amber-*`/`bg-sky-*` 금지.
- 상세 스펙: `docs/design-docs/ADR-010-ui-tokens-and-dialog-shell.md`
