'use client'

import { useTranslations } from 'next-intl'
import { BUILDER_TOKENS as T } from './builder-tokens'

export interface ClarifyingQuestionsCardProps {
  /** 헤더 라벨. 기본 `확인이 필요해요`. */
  label?: string
  /** 질문 목록 — 각 항목은 문자열 또는 [본문, 힌트] 튜플. 힌트는 muted-soft 색으로 inline 표시. */
  items: Array<string | { text: string; hint?: string }>
}

/**
 * Builder Phase 2 진행 중 표시되는 "확인이 필요해요" 카드.
 *
 * 봇 메시지 안 child block 으로 자리잡는 presentational 컴포넌트.
 * 백엔드가 별도 tool로 emit하기 시작하면 그 tool UI에서 이 컴포넌트를 wrap해 사용.
 */
export function ClarifyingQuestionsCard({
  label,
  items,
}: ClarifyingQuestionsCardProps) {
  const t = useTranslations('chat.intentSummary')
  const resolvedLabel = label ?? t('clarifyingTitle')
  return (
    <div
      className="rounded-[12px]"
      style={{
        background: T.surfaceAlt,
        border: `1px solid ${T.border}`,
        padding: '14px 16px',
      }}
    >
      <div
        className="mb-2 text-[12.5px] font-semibold"
        style={{ color: T.muted, letterSpacing: '-0.005em' }}
      >
          {resolvedLabel}
      </div>
      <ul
        className="m-0 list-disc text-[14px]"
        style={{
          paddingLeft: 18,
          lineHeight: 1.7,
          color: T.ink2,
        }}
      >
        {items.map((item, idx) => {
          const text = typeof item === 'string' ? item : item.text
          const hint = typeof item === 'string' ? undefined : item.hint
          return (
            <li key={idx}>
              {text}
              {hint && <span style={{ color: T.mutedSoft }}> ({hint})</span>}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
