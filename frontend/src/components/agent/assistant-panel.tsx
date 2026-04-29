'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { SparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useQueryClient } from '@tanstack/react-query'
import { AssistantRuntimeProvider, useComposerRuntime } from '@assistant-ui/react'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import type { Message, SSEEvent } from '@/lib/types'
import { TOOL_UI_WITHOUT_HITL } from '@/lib/chat/tool-ui-registry'
import { streamAssistant } from '@/lib/sse/stream-assistant'
import { AssistantThread } from '@/components/chat/assistant-thread'
import { FixHero } from '@/components/agent/fix-hero'

const FIX_AGENT_IMAGE = '/agent-fix-hero.webp'
const CREATE_HERO_IMAGE = '/agent-create-hero.webp'

interface AssistantPanelProps {
  agentId: string
  agentName: string
  showHeader?: boolean
  /** 만들기 모드 — agentId 비어있고 첫 메시지 시 부모가 createAgent + redirect 처리 */
  createMode?: boolean
  /** createMode일 때 첫 메시지 콜백 (부모가 createAgent + sessionStorage 보존 + redirect) */
  onCreateModeFirstMessage?: (msg: string) => Promise<void>
  /** 마운트 직후 자동 전송할 메시지 (settings 페이지 진입 시 sessionStorage carry용) */
  initialMessage?: string
}

export function AssistantPanel({
  agentId,
  agentName,
  showHeader = true,
  createMode = false,
  onCreateModeFirstMessage,
  initialMessage,
}: AssistantPanelProps) {
  const t = useTranslations('agent.assistant')
  const ts = useTranslations('agent.suggestion')
  const qc = useQueryClient()

  const sessionId = useMemo(() => crypto.randomUUID(), [])
  const [localMessages, setLocalMessages] = useState<Message[]>([])
  const initialSentRef = useRef(false)

  const onMessagesCommit = useCallback((msgs: Message[]) => {
    setLocalMessages((prev) => [...prev, ...msgs])
  }, [])

  // streamFn:
  // - createMode + agentId 비어있음 → 부모 콜백으로 createAgent + redirect 위임
  // - 그 외 → streamAssistant(agentId)
  const streamFn = useCallback(
    (content: string, signal: AbortSignal): AsyncGenerator<SSEEvent> => {
      async function* run() {
        if (createMode && !agentId) {
          if (onCreateModeFirstMessage) {
            await onCreateModeFirstMessage(content)
          }
          // 부모가 redirect 처리하므로 stream 시작 안 함 (컴포넌트 unmount 예정)
          return
        }
        yield* streamAssistant(agentId, content, signal, sessionId)
      }
      return run()
    },
    [agentId, sessionId, createMode, onCreateModeFirstMessage],
  )

  const onStreamEnd = useCallback(
    (didMutate: boolean) => {
      // 단순 텍스트/조회 응답에는 invalidate 불필요 — write 도구가 호출됐을 때만.
      if (!didMutate) return
      qc.invalidateQueries({ queryKey: ['agents'] })
      if (agentId) {
        qc.invalidateQueries({ queryKey: ['agents', agentId] })
      }
    },
    [qc, agentId],
  )

  const { runtime, sendMessage } = useChatRuntime({
    messages: localMessages,
    streamFn,
    onStreamEnd,
    onMessagesCommit,
  })

  // 마운트 후 initialMessage 자동 전송 (sessionStorage carry-over)
  useEffect(() => {
    if (initialMessage && !initialSentRef.current && agentId) {
      initialSentRef.current = true
      void sendMessage(initialMessage)
    }
  }, [initialMessage, sendMessage, agentId])

  const suggestions = useMemo(
    () => [ts('addTavily'), ts('addTodo'), ts('compactPrompt')],
    [ts],
  )

  const heroImage = createMode ? CREATE_HERO_IMAGE : FIX_AGENT_IMAGE
  const heroTitle =
    createMode || !agentName ? t('fixHeroTitleNew') : t('fixHeroTitle', { agentName })

  return (
    <div className="flex h-full min-h-0 flex-col rounded-xl border bg-background">
      {showHeader && (
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <SparklesIcon className="size-4 text-primary" />
          <h3 className="text-sm font-semibold">{t('title')}</h3>
          <span className="text-xs text-muted-foreground">
            {t('description', { agentName: agentName || ' ' })}
          </span>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        <AssistantRuntimeProvider runtime={runtime}>
          <AssistantThread
            agentImageUrl={heroImage}
            agentImagePublicAsset
            agentName={agentName}
            compact
            emptyContent={
              <EmptyContent
                imageSrc={createMode ? CREATE_HERO_IMAGE : FIX_AGENT_IMAGE}
                title={heroTitle}
                subtitle={t('fixHeroSubtitle')}
                suggestions={suggestions}
              />
            }
            toolUI={TOOL_UI_WITHOUT_HITL}
          />
        </AssistantRuntimeProvider>
      </div>
    </div>
  )
}

interface EmptyContentProps {
  title: string
  subtitle: string
  suggestions: string[]
  imageSrc?: string
}

function EmptyContent({ title, subtitle, suggestions, imageSrc }: EmptyContentProps) {
  // useComposerRuntime는 AssistantRuntimeProvider 컨텍스트 안에서만 동작
  const composer = useComposerRuntime({ optional: true })

  return (
    <div className="flex h-full flex-col items-center justify-center px-4 py-8 text-center">
      <FixHero
        imageSrc={imageSrc}
        title={title}
        subtitle={subtitle}
        suggestions={suggestions}
        onSuggestionClick={(s) => composer?.setText(s)}
      />
    </div>
  )
}
