'use client'

import { useState, useSyncExternalStore } from 'react'
import { useTranslations } from 'next-intl'
import { InfoIcon, XIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { readLocalFlag, writeLocalFlag } from '@/lib/browser/local-flag'

const DISMISS_KEY = 'moldy.memory.firstUseNotice.dismissed'

function readDismissed(): boolean {
  return readLocalFlag(DISMISS_KEY)
}

function subscribe(): () => void {
  return () => {}
}

/**
 * Plan 15 (5.9) 최초 사용 안내: personal memory reaches every agent, so ask for a mode
 * first. Dismissal is a per-browser convenience; storage failures just show it again.
 */
export function MemoryFirstUseNotice({ onChooseMode }: { onChooseMode: () => void }) {
  const t = useTranslations('appSettings.memory.firstUse')
  // Hidden on the server snapshot so hydration never flashes a dismissed notice.
  const storedDismissed = useSyncExternalStore(subscribe, readDismissed, () => true)
  const [dismissedNow, setDismissedNow] = useState(false)
  if (storedDismissed || dismissedNow) return null

  function dismiss() {
    setDismissedNow(true)
    writeLocalFlag(DISMISS_KEY)
  }

  return (
    <div
      role="note"
      className="moldy-muted-panel flex items-start gap-3 p-4"
      data-testid="memory-first-use-notice"
    >
      <InfoIcon className="mt-0.5 size-4 shrink-0 text-primary-strong" aria-hidden />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-sm font-medium text-foreground">{t('title')}</p>
        <p className="text-sm leading-6 text-muted-foreground">{t('description')}</p>
        <Button
          type="button"
          size="sm"
          onClick={onChooseMode}
          data-testid="memory-first-use-choose"
        >
          {t('choose')}
        </Button>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        onClick={dismiss}
        aria-label={t('dismiss')}
      >
        <XIcon className="size-4" aria-hidden />
      </Button>
    </div>
  )
}
