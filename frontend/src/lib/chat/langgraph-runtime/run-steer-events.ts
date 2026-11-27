import { useCallback } from 'react'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import { useQueryClient } from '@tanstack/react-query'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'
import { isRecord } from './activity-state'

const CHANNELS = ['custom'] as const

export function RunSteerStreamSync({
  stream,
  conversationId,
}: {
  readonly stream: AnyStream
  readonly conversationId: string
}) {
  const client = useQueryClient()
  const refresh = useCallback(
    (event: Event) => {
      const { method, params } = event
      const data = params?.data
      if (!isRecord(data)) return
      const name =
        method === 'custom'
          ? (data.name ?? data.channel)
          : method.startsWith('custom:')
            ? method.slice(7)
            : null
      if (name !== 'moldy.run_steer') return
      const payload = isRecord(data.payload) ? data.payload : data
      if (payload.conversation_id !== conversationId) return
      // Replayed and live events are notifications. The permission-checked
      // receipt list is authoritative even if an older event arrives last.
      void client.invalidateQueries(
        { queryKey: conversationQueryKeys.steers(conversationId), exact: true },
        { cancelRefetch: false },
      )
    },
    [client, conversationId],
  )
  useChannelEffect(stream, CHANNELS, { replay: true, bufferSize: 64, onEvent: refresh })
  return null
}
