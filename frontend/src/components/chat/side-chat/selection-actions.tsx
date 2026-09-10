'use client'

import { useEffect, useMemo, useState, type RefObject } from 'react'
import { MessageSquarePlusIcon, QuoteIcon, SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Popover, PopoverContent, PopoverTitle } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
import { readQuoteSelection, type QuoteSelection } from './quote-selection'
import { useSideChat } from './side-chat-context'

export function SelectionActions({
  rootRef,
}: {
  readonly rootRef: RefObject<HTMLDivElement | null>
}) {
  const t = useTranslations('chat.sideChat')
  const workspace = useSideChat()
  const [selection, setSelection] = useState<QuoteSelection | null>(null)
  const anchor = useMemo(
    () =>
      selection
        ? {
            getBoundingClientRect: () => selection.rect,
            contextElement: selection.source,
          }
        : null,
    [selection],
  )

  useEffect(() => {
    let frame = 0
    const inspect = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        if (rootRef.current)
          setSelection(
            readQuoteSelection(rootRef.current, window.getSelection(), workspace?.sideId),
          )
      })
    }
    const dismiss = () => setSelection(null)
    document.addEventListener('mouseup', inspect)
    document.addEventListener('keyup', inspect)
    document.addEventListener('touchend', inspect)
    document.addEventListener('scroll', dismiss, true)
    window.addEventListener('resize', dismiss)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('mouseup', inspect)
      document.removeEventListener('keyup', inspect)
      document.removeEventListener('touchend', inspect)
      document.removeEventListener('scroll', dismiss, true)
      window.removeEventListener('resize', dismiss)
    }
  }, [rootRef, workspace?.sideId])

  if (!workspace?.mainId || !selection) return null
  const send = (target: 'main' | 'side', prompt?: string) => {
    workspace.deliver({ target, reference: selection.reference, prompt })
    if (target === 'main' && window.matchMedia('(max-width: 1279px)').matches) workspace.close()
    window.getSelection()?.removeAllRanges()
    setSelection(null)
  }
  return (
    <Popover
      open
      onOpenChange={(open) => {
        if (!open) setSelection(null)
      }}
    >
      <PopoverContent
        anchor={anchor}
        align="center"
        initialFocus={false}
        finalFocus={false}
        className="flex flex-wrap items-center justify-center gap-0.5 p-1"
        onMouseDown={(event) => event.preventDefault()}
        data-testid="chat-selection-actions"
      >
        <PopoverTitle className="sr-only">{t('selectionActions')}</PopoverTitle>
        <Button size="sm" variant="ghost" onClick={() => send('main')}>
          <QuoteIcon className="size-3.5" />
          {t('addToChat')}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => send('side', t('explainPrompt'))}>
          <SparklesIcon className="size-3.5" />
          {t('explain')}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => send('side')}>
          <MessageSquarePlusIcon className="size-3.5" />
          {t('askSide')}
        </Button>
      </PopoverContent>
    </Popover>
  )
}
