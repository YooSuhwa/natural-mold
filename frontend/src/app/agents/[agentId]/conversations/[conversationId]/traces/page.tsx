'use client'

import { use } from 'react'
import dynamic from 'next/dynamic'
import { Skeleton } from '@/components/ui/skeleton'

const TraceDebuggerView = dynamic(
  () => import('@/components/chat/trace-debugger-view').then((mod) => mod.TraceDebuggerView),
  {
    ssr: false,
    loading: () => (
      <div className="flex flex-1 flex-col gap-3 p-6">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-[calc(100vh-12rem)] w-full" />
      </div>
    ),
  },
)

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
