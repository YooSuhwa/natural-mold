'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronDownIcon, ChevronRightIcon, MaximizeIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

interface SectionInstructionsProps {
  systemPrompt: string
  onSystemPromptChange: (v: string) => void
}

export function SectionInstructions({
  systemPrompt,
  onSystemPromptChange,
}: SectionInstructionsProps) {
  const t = useTranslations('agent.settings')
  const [open, setOpen] = useState(true)
  const [fullscreen, setFullscreen] = useState(false)

  const count = systemPrompt.length

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 rounded-lg border p-3">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1 text-sm font-medium"
        >
          {open ? (
            <ChevronDownIcon className="size-4 text-muted-foreground" />
          ) : (
            <ChevronRightIcon className="size-4 text-muted-foreground" />
          )}
          {t('instruction')}
        </button>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setFullscreen(true)}
          aria-label={t('instructionFullscreen')}
        >
          <MaximizeIcon className="size-4" />
        </Button>
      </div>

      {open && (
        <div className="flex min-h-0 flex-1 flex-col gap-1.5">
          <Textarea
            value={systemPrompt}
            onChange={(e) => onSystemPromptChange(e.target.value)}
            className="min-h-[200px] flex-1 resize-none font-mono text-xs shadow-none [field-sizing:fixed] focus-visible:border-input focus-visible:ring-0 focus-visible:ring-offset-0"
            placeholder={t('instructionPlaceholder')}
          />
          <div className="text-right text-xs text-muted-foreground">
            {t('characterCount', { count })}
          </div>
        </div>
      )}

      <Dialog open={fullscreen} onOpenChange={setFullscreen}>
        <DialogContent className="sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>{t('instruction')}</DialogTitle>
          </DialogHeader>
          <Textarea
            value={systemPrompt}
            onChange={(e) => onSystemPromptChange(e.target.value)}
            className="h-[70vh] resize-none font-mono text-xs shadow-none [field-sizing:fixed] focus-visible:border-input focus-visible:ring-0 focus-visible:ring-offset-0"
            placeholder={t('instructionPlaceholder')}
          />
          <div className="text-right text-xs text-muted-foreground">
            {t('characterCount', { count })}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
