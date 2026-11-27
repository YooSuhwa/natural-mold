'use client'

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from 'react'
import type {
  AgUiAssistantRuntime,
  AgUiInterrupt,
  AgUiResumeEntry,
} from '@assistant-ui/react-ag-ui'
import { authRequiredAttempt } from '@/lib/chat/auth-required-interrupt'
import type { HiTLContextValue } from '@/lib/chat/hitl-context'
import { standardPayloadFromInterrupt } from '@/lib/chat/langgraph-runtime/hitl-interrupts'
import { useHitlDecisionController } from '@/lib/chat/langgraph-runtime/use-hitl-decision-controller'
import type { Decision, StandardInterruptPayload } from '@/lib/types'

const EMPTY: readonly AgUiInterrupt[] = []
const getEmpty = () => EMPTY

// AG-UI records the outcome on the owning assistant message. It also owns
// refresh after a resume, so the coordinator needs no separate UI journal.
const finish = async () => {}
const ignoreResolved = () => {}

function isActionable(reason: string, value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  switch (reason) {
    case 'moldy.tool_approval':
      return !('type' in value) && 'action_requests' in value && 'review_configs' in value
    case 'moldy.ask_user':
      return 'type' in value && value.type === 'ask_user'
    case 'moldy.auth_required':
      return 'type' in value && value.type === 'auth_required'
    default:
      return false
  }
}

export function isReadOnlyAgUiInterrupt(interrupt: AgUiInterrupt): boolean {
  const value = interrupt.metadata?.moldy
  return !!value && typeof value === 'object' && 'actionable' in value && value.actionable === false
}

export function moldyAgUiInterruptPayloads(
  interrupts: readonly AgUiInterrupt[],
): StandardInterruptPayload[] {
  // The standard resume must cover every pending slot in a single request.
  // A retained read-only slot therefore also withholds its siblings' controls.
  if (interrupts.some(isReadOnlyAgUiInterrupt)) return []
  const current = new Map<string, StandardInterruptPayload>()
  for (const interrupt of interrupts) {
    const value = interrupt.metadata?.moldy
    if (!interrupt.id.trim() || !isActionable(interrupt.reason, value)) continue
    // Native ask_user passes its fields through to the tool UI. Keep the
    // checkpoint's access markers only on the original AG-UI interrupt.
    const toolInput = { ...value }
    delete toolInput.actionable
    delete toolInput.legacyInterruptAccess
    const payload = standardPayloadFromInterrupt({ id: interrupt.id, value: toolInput })
    if (!payload) continue
    const group = JSON.stringify([
      interrupt.subagentRunId ?? null,
      payload.auth_request_id ? 'auth' : 'interrupt',
      payload.auth_request_id ?? interrupt.id,
    ])
    const previous = current.get(group)
    if (previous && (authRequiredAttempt(previous) ?? 0) > (authRequiredAttempt(payload) ?? 0)) {
      continue
    }
    current.set(group, payload)
  }
  return [...current.values()]
}

type Runtime = Pick<
  AgUiAssistantRuntime,
  'unstable_getPendingInterrupts' | 'unstable_submitInterruptResponses'
> & { readonly thread: Pick<AgUiAssistantRuntime['thread'], 'subscribe'> }

interface Options {
  readonly runtime: Runtime
  readonly conversationId: string
  readonly agentId: string
  readonly getReadyBatchKey: (native: readonly AgUiInterrupt[]) => string | null
}

function allowsDecision(
  payload: StandardInterruptPayload,
  index: number,
  decision: Decision,
): boolean {
  const review = payload.review_configs[index]
  return (
    !!review &&
    review.action_name === payload.action_requests[index]?.name &&
    review.allowed_decisions.includes(decision.type) &&
    (decision.scope !== 'session' ||
      (decision.type === 'approve' && review.session_consent_eligible === true)) &&
    (payload.native !== 'auth_required' || decision.attempt === authRequiredAttempt(payload))
  )
}

function inactive() {
  return new DOMException('The approval request is no longer current', 'InvalidStateError')
}

export function useAgUiHitlActions({
  runtime,
  conversationId,
  agentId,
  getReadyBatchKey,
}: Options): Required<HiTLContextValue> {
  const subscribe = useCallback((notify: () => void) => runtime.thread.subscribe(notify), [runtime])
  const getPending = useCallback(() => {
    const pending = runtime.unstable_getPendingInterrupts()
    return pending.length ? pending : EMPTY
  }, [runtime])
  const pending = useSyncExternalStore(subscribe, getPending, getEmpty)
  const payloads = useMemo(() => moldyAgUiInterruptPayloads(pending), [pending])
  const byId = useMemo(() => new Map(payloads.map((p) => [p.interrupt_id, p])), [payloads])

  const readyBatchKey =
    pending.length && byId.size === pending.length ? getReadyBatchKey(pending) : null
  const owner = useMemo(
    () => ({ runtime, conversationId, agentId, readyBatchKey }),
    [runtime, conversationId, agentId, readyBatchKey],
  )
  const activeOwner = useRef<typeof owner | null>(null)
  useLayoutEffect(() => {
    return () => {
      activeOwner.current = null
    }
  }, [owner])
  // The graph can replace a slot while a card is open or the user is still
  // reviewing another card. Do not enqueue even a partial vote for that batch.
  const currentBatch = useCallback(() => {
    const current = getPending()
    if (
      activeOwner.current !== owner ||
      readyBatchKey === null ||
      getReadyBatchKey(current) !== readyBatchKey ||
      !current.length ||
      byId.size !== current.length ||
      pending.length !== current.length
    )
      throw inactive()
    for (const interrupt of current) {
      const previous = pending.find((slot) => slot.id === interrupt.id)
      if (
        isReadOnlyAgUiInterrupt(interrupt) ||
        !byId.has(interrupt.id) ||
        !previous ||
        (previous !== interrupt && JSON.stringify(previous) !== JSON.stringify(interrupt)) ||
        (interrupt.expiresAt !== undefined && !(Date.parse(interrupt.expiresAt) > Date.now()))
      ) {
        throw inactive()
      }
    }
    return current
  }, [byId, getPending, getReadyBatchKey, owner, pending, readyBatchKey])

  const submissions = useRef(new Set<string>())
  const respond = useCallback(
    async (responses: ReadonlyMap<string, { readonly decisions: Decision[] }>) => {
      const current = currentBatch()
      if (responses.size !== current.length) throw inactive()
      const entries: AgUiResumeEntry[] = current.map((interrupt) => {
        const payload = byId.get(interrupt.id)
        const response = responses.get(interrupt.id)
        if (!payload || !response) throw inactive()
        const decisions = response.decisions
        if (
          decisions.length !== payload.action_requests.length ||
          decisions.some((decision, index) => !allowsDecision(payload, index, decision))
        ) {
          throw inactive()
        }
        return { interruptId: interrupt.id, status: 'resolved', payload: { decisions } }
      })
      // The SDK may wait for a draining stream before it clears its pending set.
      // A second click in that window must never start another resume.
      const key = JSON.stringify([conversationId, agentId, current])
      if (submissions.current.has(key)) throw inactive()
      const submission = Promise.resolve().then(() => {
        currentBatch()
        return runtime.unstable_submitInterruptResponses(entries)
      })
      submissions.current.add(key)
      try {
        await submission
      } finally {
        submissions.current.delete(key)
      }
    },
    [agentId, byId, conversationId, currentBatch, runtime],
  )
  const stream = useMemo(
    () => ({
      respond: (response: { decisions: Decision[] }, target?: { interruptId: string }) =>
        respond(new Map(target ? [[target.interruptId, response]] : [])),
      respondAll: (responses: Record<string, { decisions: Decision[] }>) =>
        respond(new Map(Object.entries(responses))),
    }),
    [respond],
  )
  const actions = useHitlDecisionController({
    // A new verified checkpoint cancels votes buffered for the previous checkpoint,
    // including when the reloaded tool IDs and all visible choices are identical.
    conversationId: JSON.stringify([conversationId, agentId, readyBatchKey]),
    stream,
    interruptPayloads: payloads,
    interruptPayloadsById: byId,
    allInterruptPayloadsById: byId,
    refreshLifecycle: finish,
    updateResolvedInterrupts: ignoreResolved,
  })
  // The coordinator resets pending votes in its effect. Activate callbacks for
  // the new generation only after that reset has completed.
  useEffect(() => {
    activeOwner.current = owner
  }, [owner])
  return {
    agentId,
    canRespond: readyBatchKey !== null,
    registerDecision: async (index, decision, displayText, interruptId) => {
      currentBatch()
      const target =
        interruptId === undefined && payloads.length === 1 ? payloads[0].interrupt_id : interruptId
      const payload = target ? byId.get(target) : undefined
      if (!payload || !allowsDecision(payload, index, decision)) throw inactive()
      await actions.registerDecision(index, decision, displayText, target)
    },
    onResumeDecisions: async (decisions, displayText) => {
      currentBatch()
      if (payloads.length !== 1) throw inactive()
      await actions.onResumeDecisions(decisions, displayText, payloads[0].interrupt_id)
    },
  }
}
