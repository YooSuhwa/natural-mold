'use client'

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Message } from '@/lib/types'
import { createAgUiChatSession } from './chat-history'
import { MoldyAgUiAgent } from './moldy-agent'

interface ChatSessionOptions {
  readonly conversationId: string
  readonly serverMessages: readonly Message[]
  readonly onBeforeNewMessage?: () => void
  readonly onNewMessageAccepted?: () => void
}

function createChatConnection(initial: ChatSessionOptions) {
  let current = initial
  const { conversationId } = initial
  const agent = new MoldyAgUiAgent({
    conversationId,
    onBeforeSubmit: () => {
      session.invalidateHistoryRead()
      current.onBeforeNewMessage?.()
    },
    onAccepted: ({ publicRunId, physicalRunId }) => {
      session.onRunAccepted({ runId: publicRunId, physicalRunId })
      current.onNewMessageAccepted?.()
    },
    onRunClaimed: ({ publicRunId, physicalRunId }) => {
      session.onRunClaimed({ runId: publicRunId, physicalRunId })
    },
    onPublicEvent: (event, source) => session.onPublicEvent(event, source),
  })
  const session = createAgUiChatSession({
    conversationId,
    readSnapshot: (signal, isCurrent) => agent.readSnapshot({ signal, isCurrent }),
    getServerMessages: () => current.serverMessages,
  })
  return {
    agent,
    session,
    update(next: ChatSessionOptions) {
      current = next
    },
  }
}

/** The keyed chat owns one transport. A prop refresh must not replace an active reader. */
export function useChatSession(options: ChatSessionOptions) {
  const [connection] = useState(() => createChatConnection(options))
  useLayoutEffect(() => {
    connection.update(options)
  }, [connection, options])

  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      // React Strict Mode can immediately reattach the same component.
      queueMicrotask(() => {
        if (mounted.current) return
        connection.agent.dispose()
        connection.session.dispose()
      })
    }
  }, [connection])

  return connection
}
