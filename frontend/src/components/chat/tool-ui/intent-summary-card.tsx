'use client'

import { CheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { BUILDER_TOKENS as T } from './builder-tokens'
import { PhaseCard, PhaseCardHeader } from './phase-card'

export type IntentConfidence = 'high' | 'medium' | 'low'

export interface IntentSummaryCardProps {
  /** 에이전트 이름 (예: '한컴 뉴스 모니터'). */
  name: string
  /** 에이전트 설명 (한 단락). */
  description: string
  /** 태그 (요약 키워드, 0~N개). */
  tags?: string[]
  /** 의도 분석 확신도. 헤더 우측 라벨에 반영. */
  confidence?: IntentConfidence
  /** 헤더 phase 라벨 (예: 'Phase 2'). */
  phaseLabel?: string
}

function IntentSummaryHeader({
  confidence = 'high',
  phaseLabel = 'Phase 2',
}: {
  confidence?: IntentConfidence
  phaseLabel?: string
}) {
  const t = useTranslations('chat.intentSummary')
  return (
    <PhaseCardHeader variant="gradient">
      <span
        className="inline-flex shrink-0 items-center justify-center rounded-full text-white"
        style={{ width: 18, height: 18, background: T.primary }}
      >
        <CheckIcon className="size-2.5" strokeWidth={3.5} />
      </span>
      <span
        className="text-[12.5px] font-semibold"
        style={{ color: T.primaryInk, letterSpacing: '-0.005em' }}
      >
        {t('title')}
      </span>
      <span className="text-[11.5px]" style={{ color: T.muted }}>
        · {phaseLabel}
      </span>
      <div className="flex-1" />
      <span
        className="text-[10.5px] font-semibold uppercase tabular-nums"
        style={{ color: T.muted, letterSpacing: '0.04em' }}
      >
        {t(`confidence.${confidence}`)}
      </span>
    </PhaseCardHeader>
  )
}

function IntentLabel({ text }: { text: string }) {
  return (
    <div
      className="mb-1 text-[11.5px] font-semibold"
      style={{ color: T.muted, letterSpacing: '-0.005em' }}
    >
      {text}
    </div>
  )
}

/**
 * Phase 2 결과 카드 — 의도 분석 요약.
 *
 * 현재 backend가 별도 tool로 emit하지 않으므로 presentational 컴포넌트로만 제공.
 * 이후 builder graph가 `intent_summary` ToolMessage를 emit하면 그 tool UI에서
 * 이 컴포넌트를 그대로 wrap해서 사용한다.
 */
export function IntentSummaryCard({
  name,
  description,
  tags,
  confidence = 'high',
  phaseLabel = 'Phase 2',
}: IntentSummaryCardProps) {
  const t = useTranslations('chat.intentSummary')
  return (
    <PhaseCard header={<IntentSummaryHeader confidence={confidence} phaseLabel={phaseLabel} />}>
      <div style={{ padding: '16px 18px 18px' }}>
        <IntentLabel text={t('agentName')} />
        <div
          className="mb-3.5 text-[19px] font-bold"
          style={{ color: T.ink, letterSpacing: '-0.015em' }}
        >
          {name}
        </div>

        <IntentLabel text={t('description')} />
        <p
          className="mb-3.5 text-[14px]"
          style={{
            color: T.ink2,
            lineHeight: 1.65,
            letterSpacing: '-0.005em',
            textWrap: 'pretty',
          }}
        >
          {description}
        </p>

        {tags && tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {tags.map((tag) => (
              <span
                key={tag}
                className="text-[11.5px] font-medium"
                style={{
                  padding: '3px 9px',
                  borderRadius: 999,
                  background: T.surfaceAlt,
                  border: `1px solid ${T.border}`,
                  color: T.ink2,
                  letterSpacing: '-0.005em',
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </PhaseCard>
  )
}
