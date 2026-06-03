'use client'

import { useTranslations } from 'next-intl'
import type { SubagentPayload } from '@/lib/stores/chat-right-rail'

interface Props {
  payload: SubagentPayload
}

export function SubagentPanelContent({ payload }: Props) {
  const t = useTranslations('chat.rightRail')
  const hasInput = Boolean(payload.input && payload.input.trim().length > 0)

  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-2 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
          {t('agent')}
        </h3>
        <p className="text-sm font-medium text-foreground">{payload.agentName}</p>
      </section>

      {hasInput ? (
        <section>
          <h3 className="mb-2 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
            {t('input')}
          </h3>
          <pre className="whitespace-pre-wrap break-words rounded-md border border-border/60 bg-card p-3 text-xs leading-relaxed text-foreground/90">
            {payload.input}
          </pre>
        </section>
      ) : null}

      <section>
        <h3 className="mb-2 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
          {t('output')}
        </h3>
        <p className="rounded-md border border-dashed border-border/60 bg-muted/40 p-3 text-xs text-muted-foreground">
          {t('subagentPending')}
        </p>
      </section>

      <p className="moldy-ui-micro text-muted-foreground/70">tool_call_id: {payload.toolCallId}</p>
    </div>
  )
}
