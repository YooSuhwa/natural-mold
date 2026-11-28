'use client'

import { useAui, useAuiState } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import type { PartialModelOutput } from '@/lib/chat/langgraph-runtime/partial-model-output'

/** Draft a fresh message. The original request and tool arguments never enter the composer. */
export function PartialStreamRecovery({ output }: { readonly output: PartialModelOutput }) {
  const t = useTranslations('chat.message.partialStream')
  const aui = useAui()
  const draftUnavailable = useAuiState(
    (s) =>
      s.thread.isRunning || Boolean(s.thread.composer.text || s.thread.composer.attachments.length),
  )
  const tools = output.tools.map(({ name, state }) => t('tool', { name, state: t(state) }))
  if (!output.text && tools.length === 0) return null

  const prepareDraft = () => {
    const composer = aui.thread.composer()
    const draft = composer.getState()
    if (aui.thread.getState().isRunning || draft.text || draft.attachments.length) return
    const evidence = [output.text, ...tools, output.truncated ? t('truncated') : '']
      .filter(Boolean)
      .join('\n')
      .split('\n')
      .map((line) => `> ${line}`)
      .join('\n')
    composer.setText(`${t('instruction')}\n\n${evidence}`)
  }

  return (
    <div className="space-y-2">
      <p className="font-medium">{t('record')}</p>
      {output.text && <p className="whitespace-pre-wrap break-words">{output.text}</p>}
      {tools.length > 0 && (
        <ul className="list-inside list-disc">
          {tools.map((tool, index) => (
            <li key={index}>{tool}</li>
          ))}
        </ul>
      )}
      {output.truncated && <p className="text-muted-foreground">{t('truncated')}</p>}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={draftUnavailable}
        onClick={prepareDraft}
      >
        {t('continue')}
      </Button>
    </div>
  )
}
