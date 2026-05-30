'use client'

import { use, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeftIcon, ChevronRightIcon, HomeIcon, SparklesIcon } from 'lucide-react'
import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'

import { AssistantThread } from '@/components/chat/assistant-thread'
import { Button } from '@/components/ui/button'
import { builderApi } from '@/lib/api/builder'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { BUILDER_TOOL_UI } from '@/lib/chat/tool-ui-registry'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { streamBuilderMessage } from '@/lib/sse/stream-builder-message'
import { streamBuilderResume } from '@/lib/sse/stream-builder-resume'
import type { Decision, Message, SSEEvent } from '@/lib/types'

function WelcomeContent() {
  const t = useTranslations('agent.conversational')
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 py-12 text-center">
      <div className="flex size-16 items-center justify-center rounded-2xl bg-violet-100 text-violet-600 dark:bg-violet-950 dark:text-violet-400">
        <SparklesIcon className="size-8" />
      </div>
      <div>
        <h2 className="text-xl font-bold">{t('welcomeTitle')}</h2>
        <p className="mt-2 max-w-md text-sm text-muted-foreground">{t('welcomeDescription')}</p>
        <p className="mt-3 text-xs text-muted-foreground">{t('example')}</p>
      </div>
    </div>
  )
}

export default function ConversationalCreationPage({
  searchParams,
}: {
  searchParams: Promise<{ initialMessage?: string }>
}) {
  const { initialMessage } = use(searchParams)
  const t = useTranslations('agent.conversational')
  const router = useRouter()
  const [messages, setMessages] = useState<Message[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const completedRef = useRef(false)
  const autoSentRef = useRef(false)

  // 첫 메시지: 세션 생성 후 stream 시작 / 후속: 기존 세션으로
  const streamFn = useCallback((content: string, signal: AbortSignal): AsyncGenerator<SSEEvent> => {
    async function* run() {
      if (!sessionIdRef.current) {
        const session = await builderApi.start(content)
        sessionIdRef.current = session.id
        setSessionId(session.id)
      }
      yield* streamBuilderMessage(sessionIdRef.current!, content, signal)
    }
    return run()
  }, [])

  const resumeFn = useCallback(
    (
      decisions: Decision[],
      signal: AbortSignal,
      displayText?: string,
      interruptId?: string | null,
    ): AsyncGenerator<SSEEvent> => {
      async function* run() {
        if (!sessionIdRef.current) return
        yield* streamBuilderResume(
          sessionIdRef.current,
          decisions,
          signal,
          displayText,
          interruptId,
        )
      }
      return run()
    },
    [],
  )

  // 스트리밍 메시지를 영구 messages로 누적
  const onMessagesCommit = useCallback((commit: Message[]) => {
    setMessages((prev) => [...prev, ...commit])
  }, [])

  // Stream 종료 후 status 체크
  // - COMPLETED + agent_id → 자동 리다이렉트
  // - FAILED → 콘솔 경고 (메시지는 graph가 이미 emit했음)
  const onStreamEnd = useCallback(() => {
    const sid = sessionIdRef.current
    if (!sid || completedRef.current) return
    void (async () => {
      try {
        const session = await builderApi.getSession(sid)
        if (session.status === 'completed' && session.agent_id) {
          completedRef.current = true
          router.push(`/agents/${session.agent_id}`)
        } else if (session.status === 'failed') {
          completedRef.current = true
          console.warn('[builder] session failed:', session.error_message)
        }
      } catch (err) {
        console.error('[builder] getSession failed:', err)
      }
    })()
  }, [router])

  const { runtime, onResumeDecisions, registerDecision, sendMessage } = useChatRuntime({
    messages,
    streamFn,
    resumeFn,
    onMessagesCommit,
    onStreamEnd,
  })

  // URL ?initialMessage=... 가 있으면 한 번만 자동 전송
  useEffect(() => {
    if (initialMessage && !autoSentRef.current) {
      autoSentRef.current = true
      void sendMessage(initialMessage)
    }
  }, [initialMessage, sendMessage])

  const hitlValue = useMemo(
    () => ({ onResumeDecisions, registerDecision }),
    [onResumeDecisions, registerDecision],
  )

  return (
    <div className="flex h-screen flex-col" style={{ background: '#fafafa' }}>
      <header
        className="flex shrink-0 items-center gap-3.5 bg-white"
        style={{
          padding: '14px 28px',
          borderBottom: '1px solid oklch(0.93 0.005 163)',
        }}
      >
        <Link href="/" aria-label={t('back')}>
          <Button
            variant="outline"
            size="icon-sm"
            className="size-[30px] rounded-lg"
            style={{
              borderColor: 'oklch(0.93 0.005 163)',
              color: 'oklch(0.35 0.005 163)',
            }}
            aria-label={t('back')}
          >
            <ChevronLeftIcon className="size-3.5" strokeWidth={2} />
          </Button>
        </Link>

        <nav
          className="flex items-center gap-1.5 text-[13px]"
          style={{ color: 'oklch(0.55 0.01 163)' }}
        >
          <HomeIcon className="size-3.5 opacity-55" />
          <span>{t('breadcrumb.create')}</span>
          <ChevronRightIcon className="size-3 opacity-45" />
          <span className="font-semibold" style={{ color: 'oklch(0.18 0.005 163)' }}>
            {t('breadcrumb.conversational')}
          </span>
        </nav>

        <div className="flex-1" />

        {sessionId && (
          <div
            className="inline-flex items-center gap-1.5 font-mono text-[11px]"
            style={{
              padding: '4px 9px',
              borderRadius: 6,
              background: 'oklch(0.985 0.008 163)',
              border: '1px solid oklch(0.93 0.005 163)',
              color: 'oklch(0.55 0.01 163)',
            }}
          >
            <span
              className="rounded-full"
              style={{
                width: 6,
                height: 6,
                background: 'oklch(0.596 0.145 163.225)',
                boxShadow: '0 0 0 3px oklch(0.96 0.04 163)',
              }}
            />
            {t('sessionLabel', { id: sessionId.slice(0, 8) })}
          </div>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col">
        <AssistantRuntimeProvider runtime={runtime}>
          <HiTLContext.Provider value={hitlValue}>
            <AssistantThread
              variant="builder"
              builderModelLabel={t('builderModelLabel')}
              builderAgentSubtitle={t('builderAgentSubtitle')}
              agentName={t('builderAgentName')}
              emptyContent={<WelcomeContent />}
              toolUI={BUILDER_TOOL_UI}
            />
          </HiTLContext.Provider>
        </AssistantRuntimeProvider>
      </div>
    </div>
  )
}
