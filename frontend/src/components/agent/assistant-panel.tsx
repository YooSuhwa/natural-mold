'use client'

import { useMemo, useCallback, useState, type ComponentType } from 'react'
import { SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useQueryClient } from '@tanstack/react-query'
import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import type { Message } from '@/lib/types'
import { TOOL_UI_WITHOUT_HITL } from '@/lib/chat/tool-ui-registry'
import { streamAssistant } from '@/lib/sse/stream-assistant'
import { AssistantThread } from '@/components/chat/assistant-thread'

interface AssistantPanelProps {
  agentId: string
  agentName: string
  agentImageUrl?: string | null
}

export function AssistantPanel({ agentId, agentName, agentImageUrl }: AssistantPanelProps) {
  const t = useTranslations('agent.assistant')
  const ts = useTranslations('agent.suggestion')
  const qc = useQueryClient()

  // Stable session ID per panel instance
  const sessionId = useMemo(() => crypto.randomUUID(), [])

  // AssistantPanel은 서버에 히스토리를 저장하지 않고 세션 내 로컬 상태로 유지
  const [localMessages, setLocalMessages] = useState<Message[]>([])

  // 스트리밍 완료 시 메시지를 로컬 히스토리에 확정
  const onMessagesCommit = useCallback((msgs: Message[]) => {
    setLocalMessages((prev) => [...prev, ...msgs])
  }, [])

  // streamFn: agentId + sessionId를 바인딩
  const streamFn = useCallback(
    (content: string, signal: AbortSignal) =>
      streamAssistant(agentId, content, signal, sessionId),
    [agentId, sessionId],
  )

  // 도구 실행 후 에이전트 쿼리 무효화
  const onStreamEnd = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['agents'] }) // prefix 매칭으로 agentId 키도 포함
  }, [qc])

  const { runtime } = useChatRuntime({
    messages: localMessages,
    streamFn,
    onStreamEnd,
    onMessagesCommit,
  })

  const SUGGESTIONS = [ts('polite'), ts('concise'), ts('cost'), ts('addSearch')]
  // TODO: assistant-ui Composer에 텍스트 주입 (useComposer)

  const emptyContent = useMemo(
    () => (
      <div className="flex flex-col items-center justify-center py-8 text-center text-muted-foreground">
        <SparklesIcon className="mb-3 size-8 text-primary/40" />
        <p className="text-sm font-medium">{t('emptyState')}</p>
        <div
          className="mt-3 flex flex-wrap justify-center gap-2"
          role="group"
          aria-label={t('emptyState')}
        >
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="rounded-full border px-3 py-1 text-xs transition-colors hover:bg-accent"
              onClick={() => { /* TODO: Composer에 텍스트 주입 */ }}
            >
              {suggestion}
            </button>
          ))}
        </div>
      </div>
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, ts],
  )

  return (
    <div className="flex flex-col rounded-xl border bg-background">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <SparklesIcon className="size-4 text-primary" />
        <h3 className="text-sm font-semibold">{t('title')}</h3>
        <span className="text-xs text-muted-foreground">{t('description', { agentName })}</span>
      </div>

      <div className="min-h-[300px] max-h-[500px]">
        <AssistantRuntimeProvider runtime={runtime}>
            <AssistantThread
              agentImageUrl={agentImageUrl}
              agentName={agentName}
              compact
              emptyContent={emptyContent}
              toolUI={TOOL_UI_WITHOUT_HITL as unknown as ComponentType[]}
            />
        </AssistantRuntimeProvider>
      </div>
    </div>
  )
}
