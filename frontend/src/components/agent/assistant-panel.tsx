'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { SparklesIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useQueryClient } from '@tanstack/react-query'
import { AuiConfig, AssistantRuntimeProvider, Tools, useAui } from '@assistant-ui/react'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import type { Decision, Message, SSEEvent } from '@/lib/types'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { ALL_TOOLKIT } from '@/lib/chat/tool-ui-registry'
import { streamAssistant, streamAssistantResume } from '@/lib/sse/stream-assistant'
import { AssistantThread } from '@/components/chat/assistant-thread'
import { FixHero } from '@/components/agent/fix-hero'
import { agentQueryKeys } from '@/lib/query-keys/agents'

const FIX_AGENT_IMAGE = '/agent-fix-hero.webp'
const CREATE_HERO_IMAGE = '/agent-create-hero.webp'

interface AssistantPanelProps {
  agentId: string
  agentName: string
  showHeader?: boolean
  createMode?: boolean
  onCreateModeFirstMessage?: (msg: string) => Promise<void>
  initialMessage?: string
  session?: AssistantPanelSession
  onClose?: () => void
}

export interface AssistantPanelSession {
  readonly sessionId: string
  readonly messages: Message[]
  readonly onMessagesCommit: (messages: Message[]) => void
}

export function AssistantPanel({
  agentId,
  agentName,
  showHeader = true,
  createMode = false,
  onCreateModeFirstMessage,
  initialMessage,
  session,
  onClose,
}: AssistantPanelProps) {
  const config = AuiConfig({ tools: Tools({ toolkit: ALL_TOOLKIT }) })
  const t = useTranslations('agent.assistant')
  const tc = useTranslations('common')
  const ts = useTranslations('agent.suggestion')
  const qc = useQueryClient()

  const generatedSessionId = useMemo(() => crypto.randomUUID(), [])
  const [localMessages, setLocalMessages] = useState<Message[]>([])
  const initialSentRef = useRef(false)
  const resumeMayMutateRef = useRef(false)

  const onMessagesCommit = useCallback(
    (msgs: Message[]) => {
      if (session) {
        session.onMessagesCommit(msgs)
        return
      }
      setLocalMessages((prev) => [...prev, ...msgs])
    },
    [session],
  )
  const sessionId = session?.sessionId ?? generatedSessionId
  const messages = session?.messages ?? localMessages

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

  const resumeFn = useCallback(
    (
      decisions: Decision[],
      signal: AbortSignal,
      displayText?: string,
      interruptId?: string | null,
    ): AsyncGenerator<SSEEvent> => {
      async function* run() {
        if (createMode && !agentId) return
        resumeMayMutateRef.current = decisions.some(
          (decision) => decision.type === 'approve' || decision.type === 'edit',
        )
        yield* streamAssistantResume(
          agentId,
          decisions,
          signal,
          displayText,
          interruptId,
          sessionId,
        )
      }
      return run()
    },
    [agentId, sessionId, createMode],
  )

  const onStreamEnd = useCallback(
    (didMutate: boolean) => {
      const shouldInvalidate = didMutate || resumeMayMutateRef.current
      resumeMayMutateRef.current = false
      if (!shouldInvalidate) return
      qc.invalidateQueries({ queryKey: agentQueryKeys.all })
      if (agentId) {
        qc.invalidateQueries({ queryKey: agentQueryKeys.detail(agentId) })
      }
    },
    [qc, agentId],
  )

  const { runtime, onResumeDecisions, registerDecision, sendMessage } = useChatRuntime({
    messages,
    streamFn,
    resumeFn,
    onStreamEnd,
    onMessagesCommit,
  })
  const hitlValue = useMemo(
    () => ({ onResumeDecisions, registerDecision }),
    [onResumeDecisions, registerDecision],
  )

  useEffect(() => {
    if (initialMessage && !initialSentRef.current && agentId) {
      initialSentRef.current = true
      void sendMessage(initialMessage)
    }
  }, [initialMessage, sendMessage, agentId])

  const suggestions = useMemo(() => [ts('addTavily'), ts('addTodo'), ts('compactPrompt')], [ts])

  const heroImage = createMode ? CREATE_HERO_IMAGE : FIX_AGENT_IMAGE
  const heroTitle =
    createMode || !agentName ? t('fixHeroTitleNew') : t('fixHeroTitle', { agentName })

  return (
    <div className="moldy-card flex h-full min-h-0 flex-col">
      {showHeader && (
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <SparklesIcon className="size-4 text-primary-strong" />
          <h3 className="text-sm font-semibold">{t('title')}</h3>
          <span className="text-xs text-muted-foreground">
            {t('description', { agentName: agentName || ' ' })}
          </span>
          {onClose ? (
            <button
              type="button"
              className="ml-auto inline-flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
              onClick={onClose}
              aria-label={tc('close')}
            >
              <XIcon className="size-4" />
            </button>
          ) : null}
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        <AssistantRuntimeProvider runtime={runtime} config={config}>
          <HiTLContext.Provider value={hitlValue}>
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
            />
          </HiTLContext.Provider>
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
  // AssistantRuntimeProvider 안에서만 composer scope가 제공된다.
  const composer = useAui().optional.composer

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
