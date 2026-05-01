'use client'

import { useRef, useState, useCallback, useMemo } from 'react'
import { useExternalStoreRuntime, useExternalMessageConverter } from '@assistant-ui/react'
import { useSetAtom } from 'jotai'
import { useQueryClient } from '@tanstack/react-query'
import type {
  Message,
  SSEEvent,
  ToolCallInfo,
  InterruptPayload,
  TokenUsageBreakdown,
} from '@/lib/types'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { convertMessage } from './convert-message'
import { extractText } from './utils'
import { streamResume } from '@/lib/sse/stream-resume'
import { streamEdit } from '@/lib/sse/stream-edit'
import { streamRegenerate } from '@/lib/sse/stream-regenerate'
import { createStreamGuard } from '@/lib/sse/stream-guard'
import type { FeedbackAdapter, AttachmentAdapter } from '@assistant-ui/react'

const PHASE_TIMELINE_TOOL_NAME = 'phase_timeline'

const MUTATION_PREFIXES = [
  'add_',
  'remove_',
  'update_',
  'edit_',
  'delete_',
  'enable_',
  'disable_',
  'create_',
] as const

function isMutationToolName(name: string | undefined): boolean {
  if (!name) return false
  return MUTATION_PREFIXES.some((p) => name.startsWith(p))
}

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

interface StreamFnOptions {
  /** Pre-uploaded attachment ids that should ride along with this message. */
  attachmentIds?: string[]
}

type StreamFn = (
  content: string,
  signal: AbortSignal,
  options?: StreamFnOptions,
) => AsyncGenerator<SSEEvent>
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
  /** 스트리밍 완료 후 호출. didMutate=true면 mutation 도구가 호출되었다는 의미 (invalidate 권장) */
  onStreamEnd?: (didMutate: boolean) => void
  /** 스트리밍 메시지 확정 시 호출 — 로컬 히스토리 유지용 (AssistantPanel) */
  onMessagesCommit?: (messages: Message[]) => void
  /** interrupt 발생 시 호출 (HiTL UI 렌더링용) */
  onInterrupt?: (payload: InterruptPayload) => void
  /** resume 시 conversationId가 필요 (legacy, conversations 페이지용) */
  conversationId?: string
  /** 커스텀 resume 함수 (Builder v3 등 conversationId가 없는 컨텍스트용) */
  resumeFn?: ResumeFn
  /** Optional thumbs up/down adapter (P0-1c). */
  feedbackAdapter?: FeedbackAdapter
  /** Optional attachment adapter (P1-7). */
  attachmentAdapter?: AttachmentAdapter
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
  feedbackAdapter,
  attachmentAdapter,
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
  // SSE stream race 차단 — Edit/Regenerate fork 도중 이전 generator의 stale
  // chunk가 새 stream에 끼어드는 것을 막고, 같은 id 중복 chunk를 dedup한다.
  // ``createStreamGuard``는 순수 함수라 useState 초기값으로 안전.
  const streamGuardRef = useRef(createStreamGuard())
  const setTokenUsage = useSetAtom(sessionTokenUsageAtom)
  const queryClient = useQueryClient()

  /** B1 fix — when the user edits/regenerates we already know the new turn
   * will replace messages from ``truncateAtIndex`` onward. Optimistically
   * shorten the messages query cache so the UI doesn't show
   * ``[old chain ... + streaming new turn]`` simultaneously (the visual
   * "flicker" before refetch). The post-stream ``invalidateQueries`` from
   * ``onStreamEnd`` then re-syncs against the new active branch. */
  const truncateMessagesCache = useCallback(
    (truncateAtIndex: number) => {
      if (!conversationId) return
      queryClient.setQueryData<Message[] | undefined>(
        ['conversations', conversationId, 'messages'],
        (prev) => (prev ? prev.slice(0, truncateAtIndex) : prev),
      )
    },
    [queryClient, conversationId],
  )

  // useCallback 불필요 — abortRef/setIsRunning은 안정 참조, 이벤트 핸들러 내부에서만 호출
  function prepareStream(): { signal: AbortSignal; token: number } {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setIsRunning(true)
    // stream version 발급 — 이전 stream의 stale event는 이 token 비교로 폐기.
    const token = streamGuardRef.current.begin()
    return { signal: controller.signal, token }
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

  /** SSE 스트림 소비 공통 로직 (onNew, onResume 공유)
   *
   * ``token`` — ``prepareStream()``이 발급한 stream version. 이 stream이 진행
   * 중인 사이 사용자가 새 stream을 시작하면 (Edit/Regenerate/cancel) version이
   * 바뀌어 ``isStale(token) === true``가 되고, 이후 chunk는 모두 폐기된다.
   * AbortController로 fetch는 끊지만 이미 buffer에 yield된 chunk는 막지 못하므로
   * caller side에서 한 번 더 거른다. */
  const consumeStream = useCallback(
    async (
      stream: AsyncGenerator<SSEEvent>,
      optimisticUserMsg: Message | null,
      token: number,
    ) => {
      let accumulated = ''
      const toolCalls: ToolCallInfo[] = []
      const toolResults: Message[] = []
      const assistantId = `stream-${crypto.randomUUID()}`
      const assistantCreatedAt = new Date().toISOString()
      // W7 — message_end 시점에 채워지는 4종 토큰 사용량. assistant 메시지에
      // 박혀 푸터 hover 팝오버가 직접 참조한다.
      let messageUsage: TokenUsageBreakdown | null = null

      // tool_calls 배열은 토큰 단위로 재생성하지 않고 dirty 시점에만 스냅샷.
      // content_delta가 빈번해도 cachedToolCalls 참조가 유지되어 React.memo 자식이
      // tool_calls prop을 동일 참조로 비교 가능.
      let cachedToolCalls: ToolCallInfo[] | null = null
      let toolCallsDirty = true

      const buildStreamState = (): Message[] => {
        if (toolCallsDirty) {
          cachedToolCalls = toolCalls.length > 0 ? [...toolCalls] : null
          toolCallsDirty = false
        }
        const assistantMsg: Message = {
          id: assistantId,
          conversation_id: '',
          role: 'assistant',
          content: accumulated,
          tool_calls: cachedToolCalls,
          tool_call_id: null,
          created_at: assistantCreatedAt,
          usage: messageUsage,
        }
        const msgs: Message[] = []
        if (optimisticUserMsg) msgs.push(optimisticUserMsg)
        msgs.push(assistantMsg, ...toolResults)
        return msgs
      }

      setStreamingMessages(buildStreamState())

      try {
        for await (const event of stream) {
          // 이 stream이 stale이면(새 stream이 시작됨) 즉시 종료. AbortController로
          // fetch는 끊지만 이미 yield된 chunk는 막지 못하므로 caller side gate가 필요.
          if (streamGuardRef.current.isStale(token)) return
          // 동일 stream 내 같은 id 중복 chunk는 무시 (백엔드가 매 chunk마다
          // ``{msg_id}-{seq}`` 형식의 unique id를 발행).
          if (streamGuardRef.current.isDuplicate(event.id)) continue
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
                  toolCallsDirty = true
                  setStreamingMessages(buildStreamState())
                  break
                }
              }
              const tcId = `tc-${crypto.randomUUID()}`
              toolCalls.push({ id: tcId, name: toolName, args: params })
              toolCallsDirty = true
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
              // 토큰 사용량 업데이트 — 세션 누적 + 메시지 단위 4종 모두.
              const usage = (
                event.data as {
                  usage?: Partial<TokenUsageBreakdown>
                }
              ).usage
              if (usage) {
                const breakdown: TokenUsageBreakdown = {
                  prompt_tokens: usage.prompt_tokens ?? 0,
                  completion_tokens: usage.completion_tokens ?? 0,
                  cache_creation_tokens: usage.cache_creation_tokens ?? 0,
                  cache_read_tokens: usage.cache_read_tokens ?? 0,
                  estimated_cost: usage.estimated_cost,
                }
                messageUsage = breakdown
                setStreamingMessages(buildStreamState())
                setTokenUsage((prev) => ({
                  inputTokens: prev.inputTokens + breakdown.prompt_tokens,
                  outputTokens: prev.outputTokens + breakdown.completion_tokens,
                  cost: prev.cost + (breakdown.estimated_cost ?? 0),
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
        // streamingMessages는 즉시 비우지 않는다 — refetch가 끝나기 전 비우면 답변이
        // 화면에서 잠깐 사라졌다 다시 나타나는 깜박임이 생긴다. 아래 prevMessagesRef
        // 비교 블록이 backend messages refetch 완료 후 clear한다.
        // interrupt(HiTL)도 그래프가 일시정지된 stream 종료 — backend는 ask_user tool_call을
        // 이미 DB에 저장한 상태이므로, onStreamEnd로 messages query를 invalidate해야
        // streaming 비운 직후 UI에서 ask_user input이 사라지지 않고 fetch된 메시지로 채워진다.
        // didMutate: write 도구가 호출되었나? 호출처는 이를 보고 폼 캐시 invalidate 여부 결정.
        const didMutate = toolCalls.some((tc) => isMutationToolName(tc.name))
        onStreamEnd?.(didMutate)
      }
    },
    [onStreamEnd, onInterrupt, onMessagesCommit, setTokenUsage],
  )

  // messages가 새로 fetch되면(refetch 완료) streaming messages를 clear.
  // streaming 직후 messages → effective 전환에서 깜박임 방지.
  const prevMessagesRef = useRef(messages)
  if (prevMessagesRef.current !== messages) {
    prevMessagesRef.current = messages
    if (!isRunning && streamingMessages.length > 0) {
      setStreamingMessages([])
    }
  }

  /** P0-C — shared stream runner for new/edit/reload/resume.
   *
   * - ``streamFactory`` builds the SSE generator lazily so each handler can
   *   close over its own args (signal, conversationId, attachments, ...).
   * - ``optimisticMsg`` is the user bubble injected immediately for visual
   *   responsiveness; ``null`` for reload (assistant-only).
   * - ``truncateAtIdx`` drops cached messages from that index onward (B1 fix)
   *   to prevent the "old chain underneath new" flicker on edit/reload.
   */
  const _runStream = useCallback(
    async (
      streamFactory: (signal: AbortSignal) => AsyncGenerator<SSEEvent>,
      optimisticMsg: Message | null,
      truncateAtIdx?: number,
    ) => {
      if (truncateAtIdx !== undefined && truncateAtIdx >= 0) {
        truncateMessagesCache(truncateAtIdx)
      }
      const { signal, token } = prepareStream()
      try {
        await consumeStream(streamFactory(signal), optimisticMsg, token)
      } catch (err) {
        console.error('[useChatRuntime] Stream error:', err)
      }
    },
    [consumeStream, truncateMessagesCache],
  )

  const onNew = useCallback(
    async (appendMessage: {
      content: readonly { type: string; text?: string }[]
      attachments?: readonly { id: string }[]
    }) => {
      const content = extractText(appendMessage.content)
      if (!content && (!appendMessage.attachments || appendMessage.attachments.length === 0)) {
        return
      }
      const userMsg = createOptimisticMessage('user', content)
      const attachmentIds = appendMessage.attachments?.map((a) => a.id)
      await _runStream(
        (signal) => streamFn(content, signal, { attachmentIds }),
        userMsg,
      )
    },
    [streamFn, _runStream],
  )

  /** HiTL: interrupt 응답 후 그래프 재개 */
  const onResume = useCallback(
    async (response: unknown, displayText?: string) => {
      const userMsg = displayText ? createOptimisticMessage('user', displayText) : null
      const intrId = lastInterruptIdRef.current
      // resumeFn 우선, 없으면 conversationId 기반 streamResume fallback.
      // 둘 다 없으면 noop (이전 동작 유지).
      if (!resumeFn && !conversationId) return
      await _runStream(
        (signal) =>
          resumeFn
            ? resumeFn(response, signal, displayText, intrId)
            : streamResume(conversationId as string, response, signal),
        userMsg,
      )
    },
    [conversationId, resumeFn, _runStream],
  )

  const onCancel = useCallback(async () => {
    abortRef.current?.abort()
  }, [])

  /** M-CHAT1b — edit a user message in place via LangGraph thread fork. */
  const onEdit = useCallback(
    async (message: {
      content: readonly { type: string; text?: string }[]
      sourceId?: string | null
      parentId?: string | null
    }) => {
      const content = extractText(message.content)
      if (!content) return
      const userMsg = createOptimisticMessage('user', content)
      // B1 fix — drop everything from the edited message onward.
      const editIdx =
        conversationId && message.sourceId
          ? messages.findIndex((m) => m.id === message.sourceId)
          : -1
      const useFork = conversationId && message.sourceId
      await _runStream(
        (signal) =>
          useFork
            ? streamEdit(conversationId as string, message.sourceId as string, content, signal)
            : streamFn(content, signal),
        userMsg,
        useFork ? editIdx : undefined,
      )
    },
    [streamFn, _runStream, conversationId, messages],
  )

  /** M-CHAT1b — regenerate an assistant turn in place via LangGraph fork. */
  const onReload = useCallback(
    async (parentId: string | null) => {
      if (conversationId) {
        // Find the assistant message that is a direct child of ``parentId``
        // in the active branch — that's the one BranchPicker should treat as
        // a sibling of the new turn.
        let targetMessageId: string | undefined
        let assistantIdxInMessages = -1
        if (parentId) {
          const merged = [...messages, ...streamingMessages]
          const idx = merged.findIndex((m) => m.id === parentId)
          const next = idx >= 0 ? merged[idx + 1] : undefined
          if (next?.role === 'assistant') {
            targetMessageId = next.id
            // Index inside ``messages`` (not merged) for the cache truncate.
            assistantIdxInMessages = messages.findIndex((m) => m.id === next.id)
          }
        }
        await _runStream(
          (signal) => streamRegenerate(conversationId, targetMessageId, signal),
          null,
          assistantIdxInMessages >= 0 ? assistantIdxInMessages : undefined,
        )
        return
      }
      // No conversation context — replay the last user message.
      const merged = [...messages, ...streamingMessages]
      const lastUser = [...merged].reverse().find((m) => m.role === 'user')
      if (!lastUser?.content) return
      await _runStream((signal) => streamFn(lastUser.content, signal), null)
    },
    [messages, streamingMessages, streamFn, _runStream, conversationId],
  )

  const adapters = useMemo(() => {
    if (!feedbackAdapter && !attachmentAdapter) return undefined
    return {
      ...(feedbackAdapter ? { feedback: feedbackAdapter } : {}),
      ...(attachmentAdapter ? { attachments: attachmentAdapter } : {}),
    }
  }, [feedbackAdapter, attachmentAdapter])

  const runtime = useExternalStoreRuntime({
    messages: threadMessages,
    isRunning,
    onNew,
    onEdit,
    onReload,
    onCancel,
    adapters,
  })

  /** 외부에서 자동으로 첫 메시지를 전송할 때 사용 (e.g., URL ?initialMessage=...) */
  const sendMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return
      const { signal, token } = prepareStream()
      const userMsg = createOptimisticMessage('user', trimmed)
      try {
        await consumeStream(streamFn(trimmed, signal), userMsg, token)
      } catch (err) {
        console.error('[useChatRuntime] sendMessage error:', err)
      }
    },
    [streamFn, consumeStream],
  )

  return { runtime, onResume, sendMessage }
}
