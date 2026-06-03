/**
 * Builder Conversational UI 한정 색 토큰.
 *
 * 빌더 카드 군(ProgressRail, IntentSummary, ToolRecommendation 등)에서 공유.
 * 다른 화면에서도 재사용되면 globals.css `@theme`로 승격할 것.
 */
export const BUILDER_TOKENS = {
  surface: 'var(--builder-surface)',
  surfaceAlt: 'var(--builder-surface-alt)',
  border: 'var(--builder-border)',
  borderSoft: 'var(--builder-border-soft)',
  ink: 'var(--builder-ink)',
  ink2: 'var(--builder-ink-2)',
  muted: 'var(--builder-muted)',
  mutedSoft: 'var(--builder-muted-soft)',
  primary: 'var(--builder-primary)',
  primaryHover: 'var(--builder-primary-hover)',
  primaryDim: 'var(--builder-primary-dim)',
  primaryBg: 'var(--builder-primary-bg)',
  primaryBgSoft: 'var(--builder-primary-bg-soft)',
  primaryBgStrong: 'var(--builder-primary-bg-strong)',
  primaryInk: 'var(--builder-primary-ink)',
  bubble: 'var(--builder-bubble)',
  trackBg: 'var(--builder-track-bg)',
  connectorRest: 'var(--builder-connector-rest)',
  pendingDot: 'var(--builder-pending-dot)',
  cardShadow: 'var(--builder-card-shadow)',
  primaryShadow: 'var(--builder-primary-shadow)',
  focusShadow: 'var(--builder-focus-shadow)',
} as const

export type BuilderToken = typeof BUILDER_TOKENS
