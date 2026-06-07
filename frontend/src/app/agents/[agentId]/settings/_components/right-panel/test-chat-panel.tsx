'use client'

import { useMemo, useCallback, useState } from 'react'
import { MessageSquareIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import type { Message } from '@/lib/types'
import { TOOL_UI_WITHOUT_HITL } from '@/lib/chat/tool-ui-registry'
import { streamAssistant } from '@/lib/sse/stream-assistant'
import { AssistantThread } from '@/components/chat/assistant-thread'

interface TestChatPanelProps {
  agentId: string
  agentName: string
  agentImageUrl?: string | null
}

/**
 * 일회성 테스트 채팅 패널 — 워크벤치 우측 [테스트] 탭.
 * 세션 내 로컬 state로만 메시지를 관리한다 (서버 저장 X).
 * MVP: streamAssistant(Fix endpoint)를 재사용. 별도 ephemeral conversation
 * 엔드포인트가 생기면 streamFn만 교체하면 된다.
 */
export function TestChatPanel({ agentId, agentName, agentImageUrl }: TestChatPanelProps) {
  const t = useTranslations('agent.settings')
  const sessionId = useMemo(() => crypto.randomUUID(), [])
  const [localMessages, setLocalMessages] = useState<Message[]>([])

  const onMessagesCommit = useCallback((msgs: Message[]) => {
    setLocalMessages((prev) => [...prev, ...msgs])
  }, [])

  const streamFn = useCallback(
    (content: string, signal: AbortSignal) => streamAssistant(agentId, content, signal, sessionId),
    [agentId, sessionId],
  )

  const { runtime } = useChatRuntime({
    messages: localMessages,
    streamFn,
    onMessagesCommit,
  })

  const emptyContent = (
    <div className="flex flex-col items-center justify-center py-8 text-center text-muted-foreground">
      <MessageSquareIcon className="mb-3 size-8 text-primary-strong/40" />
      <p className="text-sm font-medium">{t('tabs.test')}</p>
    </div>
  )

  return (
    <div className="moldy-card flex h-full min-h-0 flex-col">
      <div className="moldy-status-surface moldy-status-warn border-x-0 border-t-0 px-4 py-2 text-xs">
        {t('testWarning')}
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        <AssistantRuntimeProvider runtime={runtime}>
          <AssistantThread
            agentImageUrl={agentImageUrl}
            agentName={agentName}
            compact
            emptyContent={emptyContent}
            toolUI={TOOL_UI_WITHOUT_HITL}
          />
        </AssistantRuntimeProvider>
      </div>
    </div>
  )
}
