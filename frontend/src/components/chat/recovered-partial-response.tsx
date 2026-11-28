'use client'

import { useQuery } from '@tanstack/react-query'
import { useTranslations } from 'next-intl'
import { conversationRunKeys } from '@/lib/hooks/use-conversation-runs'
import { streamSSEGetResume } from '@/lib/sse/parse-sse'
import { isRecord } from '@/lib/chat/langgraph-runtime/activity-state'

export function RecoveredPartialResponse({
  conversationId,
  runId,
}: {
  readonly conversationId: string
  readonly runId: string
}) {
  const t = useTranslations('chat.page')
  const replay = useQuery({
    queryKey: conversationRunKeys.partialOutput(conversationId, runId),
    queryFn: async ({ signal }) => {
      const chunks: string[] = []
      // This existing read-only adapter projects both v3 records and older SSE records.
      const events = streamSSEGetResume(
        `/api/conversations/${conversationId}/runs/${runId}/ag-ui-stream`,
        signal,
        {
          runId,
          defaultEvent: 'CUSTOM',
          onMode: (info) => {
            if (info.mode !== 'replay' || info.runId !== runId)
              throw new Error('Unexpected replay source')
          },
        },
      )
      for await (const { data } of events) {
        if (
          !isRecord(data) ||
          data.type !== 'TEXT_MESSAGE_CONTENT' ||
          typeof data.delta !== 'string'
        )
          continue
        const raw = data.rawEvent
        if (isRecord(raw) && Array.isArray(raw.namespace) && raw.namespace.length > 0) continue
        chunks.push(data.delta)
      }
      return chunks.join('')
    },
    gcTime: 0,
    retry: false,
  })
  return (
    <section className="mb-2 space-y-1" aria-label={t('partialOutput')}>
      <h4 className="moldy-ui-caption font-medium">{t('partialOutput')}</h4>
      <p className="whitespace-pre-wrap break-words moldy-ui-caption">
        {replay.isPending
          ? t('partialOutputLoading')
          : replay.isError || !replay.data
            ? t('partialOutputUnavailable')
            : replay.data}
      </p>
    </section>
  )
}
