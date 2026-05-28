/**
 * Builder Conversational UI 한정 색 토큰.
 *
 * 빌더 카드 군(ProgressRail, IntentSummary, ToolRecommendation 등)에서 공유.
 * 다른 화면에서도 재사용되면 globals.css `@theme`로 승격할 것.
 */
export const BUILDER_TOKENS = {
  surface: '#ffffff',
  surfaceAlt: 'oklch(0.985 0.008 163)',
  border: 'oklch(0.93 0.005 163)',
  borderSoft: 'oklch(0.96 0.005 163)',
  ink: 'oklch(0.18 0.005 163)',
  ink2: 'oklch(0.35 0.005 163)',
  muted: 'oklch(0.55 0.01 163)',
  mutedSoft: 'oklch(0.72 0.01 163)',
  primary: 'oklch(0.596 0.145 163.225)',
  primaryHover: 'oklch(0.54 0.15 163)',
  primaryDim: 'oklch(0.78 0.085 163)',
  primaryBg: 'oklch(0.96 0.04 163)',
  primaryBgStrong: 'oklch(0.92 0.06 163)',
  primaryInk: 'oklch(0.32 0.1 163)',
  bubble: 'oklch(0.95 0.045 163)',
  cardShadow: '0 1px 2px oklch(0.4 0.05 163 / 0.04)',
  primaryShadow: '0 1px 2px oklch(0.4 0.1 163 / 0.2)',
  focusShadow: '0 0 0 3px oklch(0.96 0.04 163)',
} as const

export type BuilderToken = typeof BUILDER_TOKENS
