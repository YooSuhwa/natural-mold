'use client'

import { use } from 'react'

import { TraceDebuggerView } from '@/components/chat/trace-debugger-view'

export default function ConversationTraceDebuggerPage({
  params,
}: {
  params: Promise<{ agentId: string; conversationId: string }>
}) {
  const { agentId, conversationId } = use(params)

  return (
    <TraceDebuggerView
      conversationId={conversationId}
      backHref={`/agents/${agentId}/conversations/${conversationId}`}
    />
  )
}
