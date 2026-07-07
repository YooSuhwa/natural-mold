'use client'

import { useComposerRuntime } from '@assistant-ui/react'
import { CheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

/**
 * "예시로 시험" try-hint (M7 — 목업 composer 위 dashed pill 차용).
 *
 * 인라인 시험(스펙 §1.3 결정 1)의 진입 affordance — 클릭하면 컴포저에 시험
 * 요청 프리필을 넣는다. AssistantThread의 composerHint 슬롯으로 렌더되어
 * AssistantRuntimeProvider 컨텍스트 안에 있으므로 composer runtime 접근 가능.
 */
export function SkillBuilderTryHint() {
  const t = useTranslations('skill.builderChat')
  const composer = useComposerRuntime()

  return (
    <button
      type="button"
      data-testid="builder-try-hint"
      onClick={() => composer.setText(t('tryHintPrefill'))}
      className="mb-2 inline-flex items-center gap-1.5 rounded-full border border-dashed border-border bg-background px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:border-primary-strong hover:bg-primary/20 hover:text-primary-strong"
    >
      <CheckIcon className="size-3" />
      {t('tryHint')}
    </button>
  )
}
