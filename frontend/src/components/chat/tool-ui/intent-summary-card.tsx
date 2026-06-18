'use client'

import { CheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { BuilderBody, BuilderMuted, BuilderPill } from './builder-primitives'
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
      <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-[var(--builder-primary)] text-white">
        <CheckIcon className="size-2.5" strokeWidth={3.5} />
      </span>
      <span className="moldy-ui-compact font-semibold moldy-builder-color-primary-ink">
        {t('title')}
      </span>
      <BuilderMuted className="moldy-ui-caption-plus">· {phaseLabel}</BuilderMuted>
      <div className="flex-1" />
      <span className="moldy-ui-meta font-semibold uppercase tabular-nums moldy-builder-color-muted">
        {t(`confidence.${confidence}`)}
      </span>
    </PhaseCardHeader>
  )
}

function IntentLabel({ text }: { text: string }) {
  return (
    <div className="mb-1 moldy-ui-caption-plus font-semibold moldy-builder-color-muted">{text}</div>
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
      <BuilderBody loose>
        <IntentLabel text={t('agentName')} />
        <div className="mb-3.5 moldy-ui-display-compact font-bold moldy-builder-color-ink">
          {name}
        </div>

        <IntentLabel text={t('description')} />
        <p className="mb-3.5 text-sm leading-relaxed moldy-builder-color-ink-2 [text-wrap:pretty]">
          {description}
        </p>

        {tags && tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {tags.map((tag) => (
              <BuilderPill key={tag}>{tag}</BuilderPill>
            ))}
          </div>
        )}
      </BuilderBody>
    </PhaseCard>
  )
}
