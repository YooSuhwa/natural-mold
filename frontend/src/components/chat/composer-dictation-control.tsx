'use client'

import { useEffect } from 'react'
import { ComposerPrimitive, useAui, useAuiState } from '@assistant-ui/react'
import { MicIcon, MicOffIcon, SquareIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import type { DictationAvailability } from './use-browser-dictation'

interface ComposerDictationControlProps {
  readonly availability: DictationAvailability
  readonly focusKey?: string | null
  readonly onStart: () => void
}

export function ComposerDictationControl({
  availability,
  focusKey,
  onStart,
}: ComposerDictationControlProps) {
  const aui = useAui()
  const t = useTranslations('chat.input.dictation')
  const isDictating = useAuiState((state) => state.composer.dictation != null)

  useEffect(
    () => () => {
      aui.composer.stopDictation()
    },
    [aui, focusKey],
  )

  if (availability === 'unsupported') {
    return (
      <span className="flex items-center gap-1">
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          disabled
          className="text-muted-foreground"
          aria-label={t('unsupported')}
          title={t('unsupported')}
        >
          <MicOffIcon className="size-4" />
        </Button>
        <span className="text-xs text-muted-foreground" role="status">
          {t('unsupported')}
        </span>
      </span>
    )
  }

  return (
    <span
      className="flex items-center gap-1"
      data-moldy-dictation={isDictating ? 'active' : 'idle'}
    >
      {isDictating ? (
        <ComposerPrimitive.StopDictation asChild>
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            className="text-primary-strong"
            aria-label={t('stop')}
            title={t('stop')}
          >
            <SquareIcon className="size-4" />
          </Button>
        </ComposerPrimitive.StopDictation>
      ) : (
        <ComposerPrimitive.Dictate asChild>
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            className="text-muted-foreground"
            aria-label={t('start')}
            title={t('start')}
            onClick={onStart}
          >
            <MicIcon className="size-4" />
          </Button>
        </ComposerPrimitive.Dictate>
      )}
      {isDictating ? (
        <span className="max-w-36 truncate text-xs text-muted-foreground" role="status">
          <ComposerPrimitive.DictationTranscript />
          <span className="sr-only">{t('listening')}</span>
        </span>
      ) : null}
      {availability === 'failed' ? (
        <span className="text-xs text-muted-foreground" role="status">
          {t('failed')}
        </span>
      ) : null}
    </span>
  )
}
