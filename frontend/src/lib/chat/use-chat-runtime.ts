'use client'

import { useRef, useState, useCallback, useMemo } from 'react'
import {
  useExternalStoreRuntime,
  useExternalMessageConverter,
} from '@assistant-ui/react'
import { useSetAtom } from 'jotai'
import type {
  Message,
  SSEEvent,
  ToolCallInfo,
  InterruptPayload,
} from '@/lib/types'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { convertMessage } from './convert-message'
import { extractText } from './utils'
import { streamResume } from '@/lib/sse/stream-resume'

function createOptimisticMessage(
  role: 'user' | 'assistant' | 'tool',
  content: string,
  overrides?: Partial<Message>,
): Message {
  return {
    id: `opt-${crypto.randomUUID()}`,
    conversation_id: '',
    role,
    content,
    tool_calls: null,
    tool_call_id: null,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

type StreamFn = (
  content: string,
  signal: AbortSignal,
) => AsyncGenerator<SSEEvent>

interface UseChatRuntimeOptions {
  /** TanStack Query에서 가져온 메시지 목록 */
  messages: Message[]
  /** SSE 스트리밍 함수 (streamChat 또는 streamAssistant) */
  streamFn: StreamFn
  /** 스트리밍 완료 후 호출 (쿼리 무효화 등) */
  onStreamEnd?: () => void
  /** 스트리밍 메시지 확정 시 호출 — 로컬 히스토리 유지용 (AssistantPanel) */
  onMessagesCommit?: (messages: Message[]) => void
  /** interrupt 발생 시 호출 (HiTL UI 렌더링용) */
  onInterrupt?: (payload: InterruptPayload) => void
  /** resume 시 conversationId가 필요 */
  conversationId?: string
}

/**
 * 기존 SSE 백엔드와 assistant-ui ExternalStoreRuntime을 연결하는 어댑터 훅.
 *
 * - messages: TanStack Query에서 로드한 기존 메시지
 * - streamFn: SSE AsyncGenerator (streamChat, streamAssistant 등)
 * - 내부적으로 isRunning, 스트리밍 메시지 상태를 관리
 */
export function useChatRuntime({
  messages,
  streamFn,
  onStreamEnd,
  onInterrupt,
  onMessagesCommit,
  conversationId,
}: UseChatRuntimeOptions) {
  const [isRunning, setIsRunning] = useState(false)
  const [streamingMessages, setStreamingMessages] = useState<Message[]>([])
  // streamError는 아직 caller에 노출되지 않은 setter-only 상태. 향후 UI에
  // 에러 배너를 띄울 때 사용할 자리(현재는 toast로 대체). 지금 제거하지 않고
  // setter만 유지하는 이유 = SSE 이벤트 경로에서 state transition을 잃지 않기
  // 위해서.
  const [, setStreamError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const setTokenUsage = useSetAtom(sessionTokenUsageAtom)

  // useCallback 불필요 — abortRef/setIsRunning은 안정 참조, 이벤트 핸들러 내부에서만 호출
  function prepareStream(): AbortSignal {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setIsRunning(true)
    return controller.signal
  }

  // 로드된 메시지 + 스트리밍 중인 메시지 병합
  const allMessages = useMemo(
    () => [...messages, ...streamingMessages],
    [messages, streamingMessages],
  )

  // Message[] → ThreadMessage[] 변환 (tool 메시지 자동 병합)
  const threadMessages = useExternalMessageConverter({
    callback: convertMessage,
    messages: allMessages,
    isRunning,
  })

  /** SSE 스트림 소비 공통 로직 (onNew, onResume 공유) */
  const consumeStream = useCallback(
    async (
      stream: AsyncGenerator<SSEEvent>,
      optimisticUserMsg: Message | null,
    ) => {
      let accumulated = ''
      let interrupted = false
      const toolCalls: ToolCallInfo[] = []
      const toolResults: Message[] = []
      const assistantId = `stream-${crypto.randomUUID()}`

      const buildStreamState = (): Message[] => {
        const assistantMsg: Message = {
          id: assistantId,
          conversation_id: '',
          role: 'assistant',
          content: accumulated,
          tool_calls: toolCalls.length > 0 ? [...toolCalls] : null,
          tool_call_id: null,
          created_at: new Date().toISOString(),
        }
        const msgs: Message[] = []
        if (optimisticUserMsg) msgs.push(optimisticUserMsg)
        msgs.push(assistantMsg, ...toolResults)
        return msgs
      }

      setStreamingMessages(buildStreamState())

      try {
        for await (const event of stream) {
          switch (event.event) {
            case 'content_delta': {
              accumulated += event.data.content ?? event.data.delta ?? ''
              setStreamingMessages(buildStreamState())
              break
            }
            case 'tool_call_start': {
              const tcId = `tc-${crypto.randomUUID()}`
              toolCalls.push({
                id: tcId,
                name: event.data.tool_name,
                args: event.data.parameters as Record<string, unknown>,
              })
              setStreamingMessages(buildStreamState())
              break
            }
            case 'tool_call_result': {
              const lastTc = toolCalls[toolCalls.length - 1]
              if (lastTc) {
                toolResults.push({
                  id: `tr-${crypto.randomUUID()}`,
                  conversation_id: '',
                  role: 'tool',
                  content: String(event.data.result ?? ''),
                  tool_calls: null,
                  tool_call_id: lastTc.id ?? null,
                  created_at: new Date().toISOString(),
                })
                setStreamingMessages(buildStreamState())
              }
              break
            }
            case 'interrupt': {
              // interrupt 발생 → 사용자 응답 대기 상태이므로 로딩 중지
              interrupted = true
              setIsRunning(false)
              onInterrupt?.(event.data)
              break
            }
            case 'error': {
              const errMsg = (event.data as { message?: string }).message
                ?? '에이전트 실행 중 오류가 발생했습니다.'
              setStreamError(errMsg)
              break
            }
            case 'message_end': {
              // 토큰 사용량 업데이트
              const usage = (event.data as {
                usage?: {
                  prompt_tokens?: number
                  completion_tokens?: number
                  estimated_cost?: number
                }
              }).usage
              if (usage) {
                setTokenUsage((prev) => ({
                  inputTokens: prev.inputTokens + (usage.prompt_tokens ?? 0),
                  outputTokens: prev.outputTokens + (usage.completion_tokens ?? 0),
                  cost: prev.cost + (usage.estimated_cost ?? 0),
                }))
              }
              break
            }
          }
        }
      } catch (err) {
        if (!(err instanceof DOMException && err.name === 'AbortError')) {
          throw err
        }
      } finally {
        setIsRunning(false)
        // 스트리밍 메시지 확정 → 로컬 히스토리 유지 (AssistantPanel용)
        if (onMessagesCommit) {
          const finalMsgs = buildStreamState()
          onMessagesCommit(finalMsgs)
        }
        setStreamingMessages([])
        if (!interrupted) {
          onStreamEnd?.()
        }
      }
    },
    [onStreamEnd, onInterrupt, onMessagesCommit, setTokenUsage],
  )

  const onNew = useCallback(
    async (appendMessage: { content: readonly { type: string; text?: string }[] }) => {
      const content = extractText(appendMessage.content)
      if (!content) return

      const signal = prepareStream()
      const userMsg = createOptimisticMessage('user', content)

      try {
        await consumeStream(streamFn(content, signal), userMsg)
      } catch (err) {
        console.error('[useChatRuntime] Stream error:', err)
      }
    },
    [streamFn, consumeStream],
  )

  /** HiTL: interrupt 응답 후 그래프 재개 */
  const onResume = useCallback(
    async (response: unknown, displayText?: string) => {
      if (!conversationId) return

      const signal = prepareStream()
      const userMsg = displayText ? createOptimisticMessage('user', displayText) : null

      try {
        await consumeStream(
          streamResume(conversationId, response, signal),
          userMsg,
        )
      } catch (err) {
        console.error('[useChatRuntime] Resume error:', err)
      }
    },
    [conversationId, consumeStream],
  )

  const onCancel = useCallback(async () => {
    abortRef.current?.abort()
  }, [])

  const runtime = useExternalStoreRuntime({
    messages: threadMessages,
    isRunning,
    onNew,
    onCancel,
  })

  return { runtime, onResume }
}
