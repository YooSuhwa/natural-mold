'use client'

import { use, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeftIcon, SparklesIcon } from 'lucide-react'
import { AssistantRuntimeProvider } from '@assistant-ui/react'

import { AssistantThread } from '@/components/chat/assistant-thread'
import { Button } from '@/components/ui/button'
import { builderApi } from '@/lib/api/builder'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { BUILDER_TOOL_UI } from '@/lib/chat/tool-ui-registry'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { streamBuilderMessage } from '@/lib/sse/stream-builder-message'
import { streamBuilderResume } from '@/lib/sse/stream-builder-resume'
import type { Message, SSEEvent } from '@/lib/types'

function WelcomeContent() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 py-12 text-center">
      <div className="flex size-16 items-center justify-center rounded-2xl bg-violet-100 text-violet-600 dark:bg-violet-950 dark:text-violet-400">
        <SparklesIcon className="size-8" />
      </div>
      <div>
        <h2 className="text-xl font-bold">자연어로 에이전트 만들기</h2>
        <p className="mt-2 max-w-md text-sm text-muted-foreground">
          만들고 싶은 에이전트를 자연어로 설명해주세요. 8단계로 함께 만들어나갑니다 — 의도 분석, 도구 추천,
          미들웨어, 시스템 프롬프트, 이미지 생성을 한 번에.
        </p>
        <p className="mt-3 text-xs text-muted-foreground">
          예: &quot;인터넷 검색하는 에이전트를 만들어줘&quot;, &quot;매일 아침 9시 주식 시세 알려주는 에이전트
          만들어줘&quot;
        </p>
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
  const router = useRouter()
  const [messages, setMessages] = useState<Message[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const completedRef = useRef(false)
  const autoSentRef = useRef(false)

  // 첫 메시지: 세션 생성 후 stream 시작 / 후속: 기존 세션으로
  const streamFn = useCallback(
    (content: string, signal: AbortSignal): AsyncGenerator<SSEEvent> => {
      async function* run() {
        if (!sessionIdRef.current) {
          const session = await builderApi.start(content)
          sessionIdRef.current = session.id
          setSessionId(session.id)
        }
        yield* streamBuilderMessage(sessionIdRef.current!, content, signal)
      }
      return run()
    },
    [],
  )

  const resumeFn = useCallback(
    (
      response: unknown,
      signal: AbortSignal,
      displayText?: string,
      interruptId?: string | null,
    ): AsyncGenerator<SSEEvent> => {
      async function* run() {
        if (!sessionIdRef.current) return
        yield* streamBuilderResume(
          sessionIdRef.current,
          response,
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

  const { runtime, onResume, sendMessage } = useChatRuntime({
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

  const hitlValue = useMemo(() => ({ onResume }), [onResume])

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Link href="/">
            <Button variant="ghost" size="icon-sm" aria-label="뒤로가기">
              <ArrowLeftIcon className="size-4" />
            </Button>
          </Link>
          <h1 className="text-sm font-semibold">새 에이전트 만들기 (대화형)</h1>
          {sessionId && (
            <span className="text-xs text-muted-foreground">세션 #{sessionId.slice(0, 8)}</span>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col">
        <AssistantRuntimeProvider runtime={runtime}>
          <HiTLContext.Provider value={hitlValue}>
            <AssistantThread
              agentName="에이전트 빌더"
              emptyContent={<WelcomeContent />}
              toolUI={BUILDER_TOOL_UI}
            />
          </HiTLContext.Provider>
        </AssistantRuntimeProvider>
      </div>
    </div>
  )
}
