'use client'

import { useRef, useState, useCallback, useMemo, useEffect } from 'react'
import { useExternalStoreRuntime, useExternalMessageConverter } from '@assistant-ui/react'
import { useSetAtom } from 'jotai'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import type {
  Decision,
  Message,
  MessagesEnvelope,
  SSEEvent,
  StandardInterruptPayload,
  ToolCallInfo,
  TokenUsageBreakdown,
} from '@/lib/types'
import { sessionTokenUsageAtom, reconnectStateAtom } from '@/lib/stores/chat-store'
import { convertMessage } from './convert-message'
import { extractText } from './utils'
import { streamResumeDecisions } from '@/lib/sse/stream-resume'
import { streamEdit } from '@/lib/sse/stream-edit'
import { streamRegenerate } from '@/lib/sse/stream-regenerate'
import { streamResumeAttach } from '@/lib/sse/stream-resume-attach'
import { withAutoResume } from '@/lib/sse/with-auto-resume'
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
  /** W3-out M5 — primary POST 응답 헤더 ``X-Run-Id`` 가 도착하면 1회 호출.
   *  conversation 라우터의 streamChat/Edit/Regenerate/ResumeDecisions 만 지원.
   *  그 외 streamFn 은 무시 → resume 시도 자체가 비활성. */
  onRunId?: (runId: string) => void
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
  /** W7-4 — 서버가 ``token_usages`` 합으로 발행한 conversation 누적 비용(USD).
   *  fetch 경로의 ``MessageResponse.usage``에는 ``estimated_cost``가 비어 있어서
   *  여기로 흘려야 Composer 토큰 바의 가격이 새로고침 후에도 유지된다. */
  totalCost?: number
  /** SSE 스트리밍 함수 (streamChat 또는 streamAssistant) */
  streamFn: StreamFn
  /** 스트리밍 완료 후 호출. didMutate=true면 mutation 도구가 호출되었다는 의미 (invalidate 권장) */
  onStreamEnd?: (didMutate: boolean) => void
  /** 스트리밍 메시지 확정 시 호출 — 로컬 히스토리 유지용 (AssistantPanel) */
  onMessagesCommit?: (messages: Message[]) => void
  /**
   * 표준 interrupt(`action_requests` / `review_configs` chunk) 도달 시 호출.
   * builder_v3 native interrupt(`{type:'ask_user'}`)도 backend streaming.py
   * 어댑터를 거쳐 표준 `{action_requests, review_configs}` chunk로 도착한다.
   */
  onStandardInterrupt?: (payload: StandardInterruptPayload) => void
  /** resume 시 conversationId가 필요 (conversations 페이지용) */
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
  totalCost,
  streamFn,
  onStreamEnd,
  onStandardInterrupt,
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
  // W3-out M5 — primary POST 응답 헤더 ``X-Run-Id`` 와 마지막 SSE event id.
  // GET ``/stream?run_id=&last_event_id=`` 재연결에 사용. stream 이 끝나거나
  // 새 stream 이 시작되면 ``prepareStream`` 에서 reset.
  const runIdRef = useRef<string | null>(null)
  const lastEventIdRef = useRef<string | null>(null)
  // SSE stream race 차단 — Edit/Regenerate fork 도중 이전 generator의 stale
  // chunk가 새 stream에 끼어드는 것을 막고, 같은 id 중복 chunk를 dedup한다.
  // ``createStreamGuard``는 순수 함수라 useState 초기값으로 안전.
  const streamGuardRef = useRef(createStreamGuard())
  const setTokenUsage = useSetAtom(sessionTokenUsageAtom)
  const setReconnectState = useSetAtom(reconnectStateAtom)
  const queryClient = useQueryClient()
  const tReconnect = useTranslations('chat.reconnect')

  /** B1 fix — when the user edits/regenerates we already know the new turn
   * will replace messages from ``truncateAtIndex`` onward. Optimistically
   * shorten the messages query cache so the UI doesn't show
   * ``[old chain ... + streaming new turn]`` simultaneously (the visual
   * "flicker" before refetch). The post-stream ``invalidateQueries`` from
   * ``onStreamEnd`` then re-syncs against the new active branch. */
  const truncateMessagesCache = useCallback(
    (truncateAtIndex: number) => {
      if (!conversationId) return
      // 캐시는 ``MessagesEnvelope`` ({messages, active_tip_message_id, ...}) 형태.
      // ``useMessages`` 가 ``select`` 로 ``messages`` 만 노출하므로 setQueryData
      // 는 envelope 통째로 갱신해야 한다 (이전엔 ``Message[]`` 로 가정해 prev.slice
      // TypeError 발생).
      queryClient.setQueryData<MessagesEnvelope | undefined>(
        ['conversations', conversationId, 'messages'],
        (prev) =>
          prev ? { ...prev, messages: prev.messages.slice(0, truncateAtIndex) } : prev,
      )
    },
    [queryClient, conversationId],
  )

  const prepareStream = useCallback((): { signal: AbortSignal; token: number } => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setIsRunning(true)
    runIdRef.current = null
    lastEventIdRef.current = null
    setReconnectState('idle')
    // stream version 발급 — 이전 stream의 stale event는 이 token 비교로 폐기.
    const token = streamGuardRef.current.begin()
    return { signal: controller.signal, token }
  }, [setReconnectState])

  // 로드된 메시지 + 스트리밍 중인 메시지 병합
  const allMessages = useMemo(
    () => [...messages, ...streamingMessages],
    [messages, streamingMessages],
  )

  // W7-2 — Composer 토큰 바는 ``allMessages``의 usage 합으로 derive한다.
  // 이전 동작은 SSE ``message_end``에서만 누적했으므로 새로고침/대화 전환
  // 후 atom이 0으로 reset되어 토큰 바가 사라졌다. messages가 fetch되면
  // ``MessageResponse.usage``(W7-2)에 4종이 들어 있으므로 절대값을 다시 계산.
  // 스트리밍 중에도 ``streamingMessages``의 ``messageUsage``가 합산되어 같은
  // 값이 유지된다.
  useEffect(() => {
    let inputTokens = 0
    let outputTokens = 0
    let perMessageCost = 0
    for (const m of allMessages) {
      if (!m.usage) continue
      inputTokens += m.usage.prompt_tokens
      outputTokens += m.usage.completion_tokens
      perMessageCost += m.usage.estimated_cost ?? 0
    }
    // server-side 합산값(``token_usages`` 테이블)이 있으면 우선. 없으면 메시지
    // 별 cost를 합산. fetch 경로의 메시지엔 보통 ``estimated_cost``가 비어 있어
    // 0이지만, streaming.py가 ``message_end``에 cost를 박은 streaming 메시지는
    // 살아있어 실시간 표시에도 약간 도움.
    const cost = totalCost ?? perMessageCost
    setTokenUsage({ inputTokens, outputTokens, cost })
  }, [allMessages, totalCost, setTokenUsage])

  // Message[] → ThreadMessage[] 변환 (tool 메시지 자동 병합)
  const threadMessages = useExternalMessageConverter({
    callback: convertMessage,
    messages: allMessages,
    isRunning,
  })

  /** SSE 스트림 소비 공통 로직 (onNew, onResumeDecisions 공유)
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
          // ``{msg_id}-{seq}`` 형식의 unique id를 발행). resume 시 boundary
          // 1개 중복도 같은 dedup 으로 거른다.
          if (streamGuardRef.current.isDuplicate(event.id)) continue
          // W3-out M5 — 가장 최근에 본 event id 를 기억. 끊김 시 GET
          // ``/stream?last_event_id=`` 로 그 다음부터 이어 받는다.
          if (event.id) lastEventIdRef.current = event.id
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
              setIsRunning(false)
              const data = event.data as StandardInterruptPayload
              if (data.interrupt_id) lastInterruptIdRef.current = data.interrupt_id
              onStandardInterrupt?.(data)
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
                // streamingMessages에 박힌 후 위쪽 useEffect가 토큰 바를
                // 자동 갱신한다 (allMessages.usage 합산). 별도 누적 호출
                // 불필요 — 누적 로직은 새로고침 시 atom이 0으로 reset되어
                // 토큰 바가 사라지는 회귀를 일으켰음.
                setStreamingMessages(buildStreamState())
              }
              break
            }
            case 'stale': {
              // W3-out M3 — backend broker 가 in-flight turn 중 사망해 GET
              // resume 이 DB replay 만 받은 신호. message_end 가 도착하지
              // 않았음을 의미하므로 (a) 토큰이 일부 누락됐을 수 있고 (b)
              // withAutoResume 의 자동 retry 도 더 이상 의미 없다. 인디케이터
              // 정리 + toast 알림으로 사용자가 "왜 응답이 멈췄는지" 인지하게.
              setReconnectState('idle')
              setStreamError('broker_lost')
              if (!streamGuardRef.current.isStale(token)) {
                toast.warning(tReconnect('stale'))
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
        // interrupt(HiTL)도 그래프가 일시정지된 stream 종료 — backend가 표준 미들웨어
        // 또는 builder_v3 어댑터 chunk를 이미 DB에 저장한 상태이므로, onStreamEnd로
        // messages query를 invalidate해야 streaming 비운 직후 UI에서 입력 카드가
        // 사라지지 않고 fetch된 메시지로 채워진다.
        // didMutate: write 도구가 호출되었나? 호출처는 이를 보고 폼 캐시 invalidate 여부 결정.
        const didMutate = toolCalls.some((tc) => isMutationToolName(tc.name))
        onStreamEnd?.(didMutate)
      }
    },
    [
      onStreamEnd,
      onStandardInterrupt,
      onMessagesCommit,
      setReconnectState,
      tReconnect,
    ],
  )

  // messages가 새로 fetch되면(refetch 완료) streaming messages를 clear.
  // streaming 직후 messages → effective 전환에서 깜박임 방지.
  //
  // W3-out M5 회귀 가드: turn 이 mid-stream 끊긴 경우 backend 가 finalize_turn /
  // checkpointer commit 을 못 해 ``messages`` API 에 해당 row 가 없다. 그 상태로
  // streamingMessages 를 비우면 사용자가 받은 partial 토큰이 화면에서 사라진다.
  // run_id (= assistant_msg_id) 가 refetch 결과에 있는지로 "정말 persist 됐는지"
  // 를 판정해 미커밋이면 streaming 을 그대로 유지한다.
  const prevMessagesRef = useRef(messages)
  if (prevMessagesRef.current !== messages) {
    prevMessagesRef.current = messages
    if (!isRunning && streamingMessages.length > 0) {
      const runId = runIdRef.current
      const wasPersisted = runId ? messages.some((m) => m.id === runId) : true
      if (wasPersisted) {
        setStreamingMessages([])
      } else {
        // assistant 미커밋(끊긴 turn) — partial assistant + tool 결과는 유지하되,
        // user 메시지는 보통 backend 가 POST 진입 직후 저장하므로 ``messages`` 에
        // 이미 들어있다. optimistic user copy 를 그대로 두면 user 버블이 중복으로
        // 보인다 (id 가 ``opt-{uuid}`` vs backend UUID 라 매칭 불가).
        setStreamingMessages((prev) => prev.filter((m) => m.role !== 'user'))
      }
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
      streamFactory: (
        signal: AbortSignal,
        onRunId: (id: string) => void,
      ) => AsyncGenerator<SSEEvent>,
      optimisticMsg: Message | null,
      truncateAtIdx?: number,
    ) => {
      if (truncateAtIdx !== undefined && truncateAtIdx >= 0) {
        truncateMessagesCache(truncateAtIdx)
      }
      const { signal, token } = prepareStream()
      const onRunId = (id: string) => {
        runIdRef.current = id
      }
      const primary = () => streamFactory(signal, onRunId)
      // GET ``/stream`` resume 은 conversations 라우터만 지원. builder/assistant
      // 같은 다른 streamFn 은 conversationId 가 없거나 runId 가 비어 있어
      // resumeFactory 가 ``null`` → withAutoResume 가 재시도하지 않고 throw.
      const resumeFactory = (lastEventId: string | undefined) => {
        if (!conversationId) return null
        const runId = runIdRef.current
        if (!runId) return null
        return streamResumeAttach(
          conversationId,
          runId,
          lastEventId,
          signal,
        ) as AsyncGenerator<SSEEvent>
      }
      const wrapped = withAutoResume(primary, resumeFactory, {
        signal,
        onReconnecting: () => {
          // stale stream(이미 새 turn 이 시작됨)의 retry 알림은 무시 — 새 turn
          // 의 prepareStream 이 idle 로 reset 한 상태를 다시 reconnecting 으로
          // 덮어쓰지 않게.
          if (streamGuardRef.current.isStale(token)) return
          setReconnectState('reconnecting')
        },
        onReconnected: () => {
          if (streamGuardRef.current.isStale(token)) return
          setReconnectState('idle')
        },
        onFailed: (err) => {
          // 사용자 cancel(AbortError) 또는 stale stream(Edit/Regenerate/새 turn)
          // 은 toast 무음. 두 가드 모두 통과한 진짜 실패만 사용자 알림.
          setReconnectState('idle')
          if (signal.aborted || streamGuardRef.current.isStale(token)) return
          toast.error(tReconnect('failed'))
          console.error('[useChatRuntime] Stream resume failed:', err)
        },
      })
      try {
        await consumeStream(wrapped, optimisticMsg, token)
      } catch (err) {
        console.error('[useChatRuntime] Stream error:', err)
      }
    },
    [
      consumeStream,
      truncateMessagesCache,
      conversationId,
      setReconnectState,
      tReconnect,
      prepareStream,
    ],
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
        (signal, onRunId) => streamFn(content, signal, { attachmentIds, onRunId }),
        userMsg,
      )
    },
    [streamFn, _runStream],
  )

  /**
   * HiTL: 표준 interrupt 응답 후 그래프 재개. `decisions.length`는
   * `action_requests.length`와 일치해야 미들웨어가 valid response로 인식.
   *
   * `resumeFn` 주입 시(builder 호환): 표준 wire를 모르는 builder의 자체
   * resume에 위임 — 첫 decision의 message(respond/reject) 또는 decision
   * 자체를 전달.
   */
  const onResumeDecisions = useCallback(
    async (decisions: Decision[], displayText?: string) => {
      const intrId = lastInterruptIdRef.current
      const userMsg = displayText ? createOptimisticMessage('user', displayText) : null

      if (resumeFn) {
        const first = decisions[0]
        const response: unknown =
          first?.type === 'respond' || first?.type === 'reject'
            ? (first.message ?? '')
            : first
        await _runStream(
          (signal) => resumeFn(response, signal, displayText, intrId),
          userMsg,
        )
        return
      }

      if (!conversationId) return
      await _runStream(
        (signal, onRunId) =>
          streamResumeDecisions(conversationId, decisions, signal, { onRunId }),
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
        (signal, onRunId) =>
          useFork
            ? streamEdit(
                conversationId as string,
                message.sourceId as string,
                content,
                signal,
                { onRunId },
              )
            : streamFn(content, signal, { onRunId }),
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
          (signal, onRunId) =>
            streamRegenerate(conversationId, targetMessageId, signal, { onRunId }),
          null,
          assistantIdxInMessages >= 0 ? assistantIdxInMessages : undefined,
        )
        return
      }
      // No conversation context — replay the last user message.
      const merged = [...messages, ...streamingMessages]
      const lastUser = [...merged].reverse().find((m) => m.role === 'user')
      if (!lastUser?.content) return
      await _runStream(
        (signal, onRunId) => streamFn(lastUser.content, signal, { onRunId }),
        null,
      )
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
      await _runStream(
        (signal, onRunId) => streamFn(trimmed, signal, { onRunId }),
        createOptimisticMessage('user', trimmed),
      )
    },
    [streamFn, _runStream],
  )

  return { runtime, onResumeDecisions, sendMessage }
}
