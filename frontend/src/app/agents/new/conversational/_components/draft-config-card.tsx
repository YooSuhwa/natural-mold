'use client'

import { useState } from 'react'
import {
  SparklesIcon,
  WrenchIcon,
  ShieldIcon,
  Loader2Icon,
  ChevronDownIcon,
  ChevronUpIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { MarkdownContent } from '@/components/chat/markdown-content'
import type { BuilderDraftConfig } from '@/lib/types'

export function DraftConfigCard({
  draft,
  onConfirm,
  isConfirming,
}: {
  draft: BuilderDraftConfig
  onConfirm: () => void
  isConfirming: boolean
}) {
  const t = useTranslations('agent.creation')
  const [promptOpen, setPromptOpen] = useState(false)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <SparklesIcon className="size-4 text-primary" />
          {t('configComplete')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2.5 rounded-lg bg-muted/50 p-4 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t('draftName')}</span>
            <span className="font-medium">{draft.name}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t('draftNameKo')}</span>
            <span className="font-medium">{draft.name_ko}</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="shrink-0 text-muted-foreground">{t('draftDescription')}</span>
            <span className="text-right">{draft.description}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t('draftModel')}</span>
            <span>{draft.model_name}</span>
          </div>
          {draft.primary_task_type && (
            <div className="flex justify-between gap-4">
              <span className="shrink-0 text-muted-foreground">{t('draftTaskType')}</span>
              <span className="text-right">{draft.primary_task_type}</span>
            </div>
          )}
        </div>

        {draft.tools.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-sm font-medium">
              {t('includedTools', { count: draft.tools.length })}
            </h4>
            <div className="space-y-1.5">
              {draft.tools.map((name) => (
                <div
                  key={name}
                  className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm"
                >
                  <WrenchIcon className="size-3.5 text-muted-foreground" />
                  {name}
                </div>
              ))}
            </div>
          </div>
        )}

        {draft.middlewares.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-sm font-medium">
              {t('includedMiddlewares', { count: draft.middlewares.length })}
            </h4>
            <div className="space-y-1.5">
              {draft.middlewares.map((name) => (
                <div
                  key={name}
                  className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm"
                >
                  <ShieldIcon className="size-3.5 text-muted-foreground" />
                  {name}
                </div>
              ))}
            </div>
          </div>
        )}

        {draft.system_prompt && (
          <div>
            <button
              type="button"
              onClick={() => setPromptOpen((v) => !v)}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {promptOpen ? (
                <ChevronUpIcon className="size-4" />
              ) : (
                <ChevronDownIcon className="size-4" />
              )}
              {t('viewSystemPrompt')}
            </button>
            {promptOpen && (
              <div className="mt-2 max-h-64 overflow-auto rounded-lg bg-muted p-3 text-sm leading-relaxed">
                <MarkdownContent content={draft.system_prompt} />
              </div>
            )}
          </div>
        )}

        <Button onClick={onConfirm} disabled={isConfirming} className="w-full" size="lg">
          {isConfirming && <Loader2Icon className="mr-1.5 size-4 animate-spin" />}
          {t('createAgent')}
        </Button>
      </CardContent>
    </Card>
  )
}
