'use client'

import { useEffect, useRef, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import type { AnyStream } from '@langchain/react'
import { SubagentRuntimeProvider } from '@/lib/chat/langgraph-runtime/subagent-runtime'
import { useAuiState } from '@assistant-ui/react'
import { SubagentTeamStrip } from './subagent-team-strip'
import { useChildTasks } from '@/lib/hooks/use-child-tasks'

export function DelegatedChildTasks({ conversationId }: { readonly conversationId: string }) {
  const running = useAuiState((state) => state.thread.isRunning)
  const t = useTranslations('chat.childTasks')
  const list = useChildTasks(conversationId, running)
  const { refetch } = list
  const previouslyRunning = useRef(running)

  useEffect(() => {
    // Short delegations can finish between two polls.
    if (previouslyRunning.current && !running) void refetch()
    previouslyRunning.current = running
  }, [running, refetch])

  return (
    <>
      <SubagentTeamStrip tasks={list.tasks} allowLegacy={list.allowLegacyDetails} />
      {list.hasNextPage && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="self-center"
          disabled={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          {t('older')}
        </Button>
      )}
    </>
  )
}

interface ScopedRuntimeProps {
  readonly conversationId?: string
  readonly stream?: AnyStream | null
  readonly children: ReactNode
}

export function ScopedSubagentRuntimeProvider(props: ScopedRuntimeProps) {
  if (!props.conversationId)
    return <SubagentRuntimeProvider>{props.children}</SubagentRuntimeProvider>
  return <KnownConversationRuntime {...props} conversationId={props.conversationId} />
}

function KnownConversationRuntime({
  conversationId,
  stream,
  children,
}: ScopedRuntimeProps & { conversationId: string }) {
  const { allowLegacyDetails } = useChildTasks(conversationId)
  // SDK discovery indexes by tool-call id across the conversation and lacks a
  // parent run id. Its raw details are usable only for an explicitly legacy chat.
  return (
    <SubagentRuntimeProvider stream={allowLegacyDetails ? stream : null}>
      {children}
    </SubagentRuntimeProvider>
  )
}
