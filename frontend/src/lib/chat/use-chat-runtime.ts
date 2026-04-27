'use client'

import { useRef, useState, useCallback, useMemo } from 'react'
import { useExternalStoreRuntime, useExternalMessageConverter } from '@assistant-ui/react'
import { useSetAtom } from 'jotai'
import type { Message, SSEEvent, ToolCallInfo, InterruptPayload } from '@/lib/types'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { convertMessage } from './convert-message'
import { extractText } from './utils'
import { streamResume } from '@/lib/sse/stream-resume'

const PHASE_TIMELINE_TOOL_NAME = 'phase_timeline'

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

type StreamFn = (content: string, signal: AbortSignal) => AsyncGenerator<SSEEvent>
type ResumeFn = (
  response: unknown,
  signal: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
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
  /** resume 시 conversationId가 필요 (legacy, conversations 페이지용) */
  conversationId?: string
  /** 커스텀 resume 함수 (Builder v3 등 conversationId가 없는 컨텍스트용) */
  resumeFn?: ResumeFn
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
  resumeFn,
}: UseChatRuntimeOptions) {
  const [isRunning, setIsRunning] = useState(false)
  const [streamingMessages, setStreamingMessages] = useState<Message[]>([])
  // streamError는 아직 caller에 노출되지 않은 setter-only 상태. 향후 UI에
  // 에러 배너를 띄울 때 사용할 자리(현재는 toast로 대체). 지금 제거하지 않고
  // setter만 유지하는 이유 = SSE 이벤트 경로에서 state transition을 잃지 않기
  // 위해서.
  const [, setStreamError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  // 가장 최근에 emit된 interrupt_id (resume 시 stale 검증용)
  const lastInterruptIdRef = useRef<string | null>(null)
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
    async (stream: AsyncGenerator<SSEEvent>, optimisticUserMsg: Message | null) => {
      let accumulated = ''
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
              const toolName = event.data.tool_name
              const params = event.data.parameters as Record<string, unknown>
              // phase_timeline은 단일 카드 갱신 (불변 패턴 — 같은 인덱스에 새 객체로 교체)
              if (toolName === PHASE_TIMELINE_TOOL_NAME) {
                const idx = toolCalls.findIndex((tc) => tc.name === PHASE_TIMELINE_TOOL_NAME)
                if (idx >= 0) {
                  toolCalls[idx] = { ...toolCalls[idx], args: params }
                  setStreamingMessages(buildStreamState())
                  break
                }
              }
              const tcId = `tc-${crypto.randomUUID()}`
              toolCalls.push({ id: tcId, name: toolName, args: params })
              setStreamingMessages(buildStreamState())
              break
            }
            case 'tool_call_result': {
              const eventToolName = (event.data as { tool_name?: string }).tool_name
              const resultStr = String(event.data.result ?? '')

              // phase_timeline result는 tool_name 기반으로 매칭 (lastTc 의존 X)
              // — 다른 tool이 사이에 emit되어도 정확히 timeline 카드만 갱신
              if (eventToolName === PHASE_TIMELINE_TOOL_NAME) {
                const tcIdx = toolCalls
                  .map((tc, i) => (tc.name === PHASE_TIMELINE_TOOL_NAME ? i : -1))
                  .filter((i) => i >= 0)
                  .pop()
                if (tcIdx !== undefined) {
                  const tc = toolCalls[tcIdx]
                  const trIdx = toolResults.findIndex((tr) => tr.tool_call_id === tc.id)
                  if (trIdx >= 0) {
                    toolResults[trIdx] = { ...toolResults[trIdx], content: resultStr }
                  } else {
                    toolResults.push({
                      id: `tr-${crypto.randomUUID()}`,
                      conversation_id: '',
                      role: 'tool',
                      content: resultStr,
                      tool_calls: null,
                      tool_call_id: tc.id ?? null,
                      created_at: new Date().toISOString(),
                    })
                  }
                  setStreamingMessages(buildStreamState())
                  break
                }
              }

              // 일반 tool: 마지막 tool_call에 매칭
              const lastTc = toolCalls[toolCalls.length - 1]
              if (!lastTc) break
              toolResults.push({
                id: `tr-${crypto.randomUUID()}`,
                conversation_id: '',
                role: 'tool',
                content: resultStr,
                tool_calls: null,
                tool_call_id: lastTc.id ?? null,
                created_at: new Date().toISOString(),
              })
              setStreamingMessages(buildStreamState())
              break
            }
            case 'interrupt': {
              // interrupt 발생 → 사용자 응답 대기 상태이므로 로딩 중지
              const intrId = (event.data as { interrupt_id?: string }).interrupt_id
              if (intrId) lastInterruptIdRef.current = intrId
              setIsRunning(false)
              onInterrupt?.(event.data)
              break
            }
            case 'error': {
              const errMsg =
                (event.data as { message?: string }).message ??
                '에이전트 실행 중 오류가 발생했습니다.'
              setStreamError(errMsg)
              break
            }
            case 'message_end': {
              // 토큰 사용량 업데이트
              const usage = (
                event.data as {
                  usage?: {
                    prompt_tokens?: number
                    completion_tokens?: number
                    estimated_cost?: number
                  }
                }
              ).usage
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
        // interrupt(HiTL)도 그래프가 일시정지된 stream 종료 — backend는 ask_user tool_call을
        // 이미 DB에 저장한 상태이므로, onStreamEnd로 messages query를 invalidate해야
        // streaming 비운 직후 UI에서 ask_user input이 사라지지 않고 fetch된 메시지로 채워진다.
        // (interrupted 시 호출 안 하던 이전 동작이 이 버그의 원인이었음)
        onStreamEnd?.()
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
      const signal = prepareStream()
      const userMsg = displayText ? createOptimisticMessage('user', displayText) : null

      // resumeFn 우선, 없으면 conversationId 기반 streamResume fallback
      const intrId = lastInterruptIdRef.current
      let stream: AsyncGenerator<SSEEvent> | null = null
      if (resumeFn) {
        stream = resumeFn(response, signal, displayText, intrId)
      } else if (conversationId) {
        stream = streamResume(conversationId, response, signal)
      }

      if (!stream) return

      try {
        await consumeStream(stream, userMsg)
      } catch (err) {
        console.error('[useChatRuntime] Resume error:', err)
      }
    },
    [conversationId, resumeFn, consumeStream],
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

  /** 외부에서 자동으로 첫 메시지를 전송할 때 사용 (e.g., URL ?initialMessage=...) */
  const sendMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return
      const signal = prepareStream()
      const userMsg = createOptimisticMessage('user', trimmed)
      try {
        await consumeStream(streamFn(trimmed, signal), userMsg)
      } catch (err) {
        console.error('[useChatRuntime] sendMessage error:', err)
      }
    },
    [streamFn, consumeStream],
  )

  return { runtime, onResume, sendMessage }
}
