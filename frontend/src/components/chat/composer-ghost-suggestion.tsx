'use client'

import { useTranslations } from 'next-intl'

/**
 * Follow-up 고스트 — 빈 컴포저 위에 제안 1개를 placeholder처럼 연하게 띄운다
 * (fish autosuggestion). 타이핑이 시작되면 부모(ThreadComposer)가 컴포저
 * 비어있음 조건으로 자동 숨기므로 이 컴포넌트는 표시만 담당한다.
 *
 * 텍스트 부분만 클릭 가능(pointer-events-auto) — → 키가 없는 터치 환경의
 * 수락 경로. 나머지 영역 클릭은 textarea 포커스로 그대로 통과한다.
 * 스크린리더에는 버튼 레이블로 제안 전문이 전달되고, 시각 장식(키캡 힌트)은
 * aria-hidden 처리한다.
 */
export function ComposerGhostSuggestion({
  text,
  onAccept,
}: {
  readonly text: string
  readonly onAccept: () => void
}) {
  const t = useTranslations('chat.followup')
  return (
    <div
      className="pointer-events-none absolute inset-0 flex items-start overflow-hidden px-3.5 py-2.5"
      data-moldy-followup-ghost="true"
    >
      <button
        type="button"
        tabIndex={-1}
        onClick={onAccept}
        aria-label={t('acceptLabel', { text })}
        className="pointer-events-auto flex min-w-0 items-baseline gap-2 text-left"
      >
        <span className="truncate text-sm leading-relaxed text-muted-foreground/70">{text}</span>
        <kbd
          aria-hidden
          className="shrink-0 rounded border border-border/60 bg-muted px-1 py-0.5 font-sans moldy-ui-micro text-muted-foreground"
        >
          {t('hint')}
        </kbd>
      </button>
    </div>
  )
}
