'use client'

import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { BUILDER_TOKENS as T } from './builder-tokens'

interface PhaseCardProps {
  /** Header strip (e.g. IntentSummaryHeader, ToolRecommendationHeader). */
  header: ReactNode
  /** Body slot — main content. */
  children: ReactNode
  /** Footer action row (optional). */
  footer?: ReactNode
  className?: string
}

/**
 * Builder Phase 결과 카드 공통 쉘.
 *
 * Phase 2~8 결과 카드(IntentSummary, ToolRecommendation, MiddlewareRecommendation,
 * SystemPrompt, ImagePreview, DraftConfig …)의 공통 외피.
 *
 * Spec:
 *  - bg `--surface` / 1px border `--border` / radius 14
 *  - shadow `0 1px 2px oklch(0.4 0.05 163 / 0.04)`
 *  - overflow hidden — header strip이 카드 모서리까지 닿게
 */
export function PhaseCard({ header, children, footer, className }: PhaseCardProps) {
  return (
    <div
      className={cn('overflow-hidden rounded-[14px]', className)}
      style={{
        background: T.surface,
        border: `1px solid ${T.border}`,
        boxShadow: T.cardShadow,
      }}
    >
      {header}
      <div>{children}</div>
      {footer}
    </div>
  )
}

interface PhaseCardHeaderProps {
  children: ReactNode
  /** 'gradient' = mint gradient (IntentSummary), 'plain' = bottom border only (ToolRecommendation). */
  variant?: 'gradient' | 'plain'
  className?: string
}

/**
 * 카드 헤더 스트립.
 *
 * - gradient: 민트 그라데이션 + bottom border (Phase 완료 결과 카드 — 의도 수집 완료 등)
 * - plain: white + bottom border (review/approval 카드 — 도구 추천 등)
 */
export function PhaseCardHeader({ children, variant = 'plain', className }: PhaseCardHeaderProps) {
  const background =
    variant === 'gradient'
      ? `linear-gradient(90deg, ${T.primaryBg} 0%, oklch(0.97 0.025 163) 100%)`
      : T.surface

  return (
    <div
      className={cn('flex items-center gap-2', className)}
      style={{
        padding: variant === 'gradient' ? '11px 16px' : '12px 16px',
        background,
        borderBottom: `1px solid ${T.border}`,
      }}
    >
      {children}
    </div>
  )
}

interface PhaseCardFooterProps {
  children: ReactNode
  className?: string
}

/**
 * 카드 푸터 영역 (action row).
 *
 * surfaceAlt 배경 + top border. 내부 padding/레이아웃은 사용처가 자유롭게 정의.
 */
export function PhaseCardFooter({ children, className }: PhaseCardFooterProps) {
  return (
    <div
      className={cn(className)}
      style={{
        borderTop: `1px solid ${T.border}`,
        background: T.surfaceAlt,
      }}
    >
      {children}
    </div>
  )
}
