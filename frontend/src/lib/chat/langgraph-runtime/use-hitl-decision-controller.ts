'use client'

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { resolvedInterruptToolCallsFromDecisions } from './hitl-interrupts'
import { stableString } from './message-list'
import { createHiTLDecisionCoordinator, type HiTLDecisionCoordinator } from '../standard-interrupt'
import type { Decision, StandardInterruptPayload } from '@/lib/types'
import type { ResolvedInterruptToolCall } from './hitl-interrupts'
import { reportRuntimeFailure } from './runtime-warning'

interface HitlStreamActions {
  readonly hydrationPromise?: Promise<void>
  respond(
    response: { readonly decisions: Decision[] },
    options?: { readonly interruptId: string; readonly namespace?: string[] },
  ): Promise<unknown>
  respondAll(responses: Record<string, { readonly decisions: Decision[] }>): Promise<unknown>
}

interface UseHitlDecisionControllerOptions {
  readonly conversationId: string
  readonly stream: HitlStreamActions
  readonly interruptPayloads: readonly StandardInterruptPayload[]
  readonly interruptPayloadsById: ReadonlyMap<string, StandardInterruptPayload>
  readonly allInterruptPayloadsById: ReadonlyMap<string, StandardInterruptPayload>
  readonly refreshLifecycle: () => Promise<void>
  readonly updateResolvedInterrupts: (
    updater: (
      current: readonly ResolvedInterruptToolCall[],
    ) => readonly ResolvedInterruptToolCall[],
  ) => void
}

interface PendingConcurrentInterruptBatch {
  readonly promise: Promise<void>
  readonly resolve: () => void
  readonly reject: (reason: unknown) => void
  readonly getPayloadKeys: () => readonly string[]
  readonly replacePayloadKeys: (payloadKeys: readonly string[]) => void
  started: boolean
  settled: boolean
}

interface PendingDecisionEntry {
  readonly decisions: Decision[]
  readonly owner: PendingConcurrentInterruptBatch
}

function deletePendingDecisionIfOwned(
  pendingDecisions: Map<string, PendingDecisionEntry>,
  payloadKey: string,
  owner: PendingConcurrentInterruptBatch,
): void {
  if (pendingDecisions.get(payloadKey)?.owner === owner) pendingDecisions.delete(payloadKey)
}

function createPendingConcurrentInterruptBatch(
  payloadKeys: readonly string[],
): PendingConcurrentInterruptBatch {
  let resolve!: () => void
  let reject!: (reason: unknown) => void
  let currentPayloadKeys = [...payloadKeys]
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return {
    promise,
    resolve,
    reject,
    getPayloadKeys: () => currentPayloadKeys,
    replacePayloadKeys: (nextPayloadKeys) => {
      currentPayloadKeys = [...nextPayloadKeys]
    },
    started: false,
    settled: false,
  }
}

function concurrentInterruptBatchKey(
  generation: number,
  payloads: readonly StandardInterruptPayload[],
): string {
  return `${generation}:${stableString(payloads.map(payloadGenerationKey).sort())}`
}

export function useHitlDecisionController({
  conversationId,
  stream,
  interruptPayloads,
  interruptPayloadsById,
  allInterruptPayloadsById,
  refreshLifecycle,
  updateResolvedInterrupts,
}: UseHitlDecisionControllerOptions) {
  const coordinatorsRef = useRef(new Map<string, HiTLDecisionCoordinator>())
  const pendingDecisionsRef = useRef(new Map<string, PendingDecisionEntry>())
  const concurrentBatchesRef = useRef(new Map<string, PendingConcurrentInterruptBatch>())
  const inFlightFlushKeysRef = useRef(new Set<string>())
  const generationRef = useRef(0)
  const activeGenerationById = useMemo(
    () =>
      new Map(
        interruptPayloads.map((payload) => [payload.interrupt_id, payloadGenerationKey(payload)]),
      ),
    [interruptPayloads],
  )
  const activeGenerationByIdRef = useRef(activeGenerationById)
  useLayoutEffect(() => {
    activeGenerationByIdRef.current = activeGenerationById
  }, [activeGenerationById])

  useEffect(() => {
    generationRef.current += 1
    const reason = new DOMException('Pending HiTL decisions were replaced', 'AbortError')
    for (const coordinator of coordinatorsRef.current.values()) coordinator.cancel(reason)
    coordinatorsRef.current.clear()
    for (const [key, batch] of concurrentBatchesRef.current) {
      if (batch.started) continue
      batch.reject(reason)
      concurrentBatchesRef.current.delete(key)
    }
    pendingDecisionsRef.current.clear()
    inFlightFlushKeysRef.current.clear()
  }, [conversationId])

  const rememberResolvedInterrupt = useCallback(
    (
      generation: number,
      payloadKey: string,
      interruptId: string,
      decisions: readonly Decision[],
      capturedPayload: StandardInterruptPayload,
    ) => {
      if (generationRef.current !== generation) return
      if (activeGenerationByIdRef.current.get(interruptId) !== payloadKey) return
      const resolved = resolvedInterruptToolCallsFromDecisions(capturedPayload, decisions)
      if (resolved.length === 0) return
      const resolvedIds = new Set(resolved.map((item) => item.toolCall.id).filter(Boolean))
      updateResolvedInterrupts((current) => [
        ...current.filter((item) => !item.toolCall.id || !resolvedIds.has(item.toolCall.id)),
        ...resolved,
      ])
    },
    [updateResolvedInterrupts],
  )

  const flushPendingDecisions = useCallback(
    async (activePayloads: readonly StandardInterruptPayload[]): Promise<boolean> => {
      if (activePayloads.length === 0) return false
      const generation = generationRef.current
      const batchKey = concurrentInterruptBatchKey(generation, activePayloads)
      const concurrentBatch = concurrentBatchesRef.current.get(batchKey)
      if (!concurrentBatch) return false
      if (concurrentBatch?.started) return true
      const decisionsById = new Map<string, Decision[]>()
      const generationKeyById = new Map<string, string>()
      for (const payload of activePayloads) {
        const activeId = payload.interrupt_id
        const generationKey = payloadGenerationKey(payload)
        const pending = pendingDecisionsRef.current.get(generationKey)
        if (!pending || pending.owner !== concurrentBatch) return false
        decisionsById.set(activeId, pending.decisions)
        generationKeyById.set(activeId, generationKey)
      }

      const flushKey = batchKey
      if (inFlightFlushKeysRef.current.has(flushKey)) return false
      inFlightFlushKeysRef.current.add(flushKey)
      try {
        if (activePayloads.length === 1) {
          const activeId = activePayloads[0]?.interrupt_id
          const decisions = activeId ? decisionsById.get(activeId) : undefined
          const payload = activeId ? allInterruptPayloadsById.get(activeId) : undefined
          const payloadKey = activeId ? generationKeyById.get(activeId) : undefined
          if (!activeId || !decisions || !payload || !payloadKey) return false
          const options = payload.namespace
            ? { interruptId: activeId, namespace: payload.namespace }
            : { interruptId: activeId }
          if (concurrentBatch) {
            concurrentBatch.started = true
            for (const payloadKey of concurrentBatch.getPayloadKeys()) {
              deletePendingDecisionIfOwned(pendingDecisionsRef.current, payloadKey, concurrentBatch)
            }
          }
          if (stream.hydrationPromise) await stream.hydrationPromise
          await stream.respond({ decisions }, options)
          if (concurrentBatch && !concurrentBatch.settled) {
            concurrentBatch.settled = true
            concurrentBatchesRef.current.delete(batchKey)
            concurrentBatch.resolve()
          }
          try {
            await refreshLifecycle()
          } catch (caught: unknown) {
            reportRuntimeFailure(caught, 'hitl_refresh_failed')
            return true
          }
          if (generationRef.current !== generation) return true
          if (activeGenerationByIdRef.current.get(activeId) !== payloadKey) return true
          deletePendingDecisionIfOwned(pendingDecisionsRef.current, payloadKey, concurrentBatch)
          rememberResolvedInterrupt(generation, payloadKey, activeId, decisions, payload)
          return true
        }

        const responsesById: Record<string, { decisions: Decision[] }> = {}
        const payloadsAtDecision = new Map<string, StandardInterruptPayload>()
        for (const [activeId, decisions] of decisionsById) {
          const payload = allInterruptPayloadsById.get(activeId)
          if (!payload) return false
          responsesById[activeId] = { decisions }
          payloadsAtDecision.set(activeId, payload)
        }
        if (concurrentBatch) {
          concurrentBatch.started = true
          for (const payloadKey of concurrentBatch.getPayloadKeys()) {
            deletePendingDecisionIfOwned(pendingDecisionsRef.current, payloadKey, concurrentBatch)
          }
        }
        if (stream.hydrationPromise) await stream.hydrationPromise
        await stream.respondAll(responsesById)
        if (concurrentBatch && !concurrentBatch.settled) {
          concurrentBatch.settled = true
          concurrentBatchesRef.current.delete(batchKey)
          concurrentBatch.resolve()
        }
        try {
          await refreshLifecycle()
        } catch (caught: unknown) {
          reportRuntimeFailure(caught, 'hitl_refresh_failed')
          return true
        }
        if (generationRef.current !== generation) return true
        for (const [activeId, decisions] of decisionsById) {
          const payload = payloadsAtDecision.get(activeId)
          const payloadKey = generationKeyById.get(activeId)
          if (!payload || !payloadKey) continue
          if (activeGenerationByIdRef.current.get(activeId) !== payloadKey) continue
          deletePendingDecisionIfOwned(pendingDecisionsRef.current, payloadKey, concurrentBatch)
          rememberResolvedInterrupt(generation, payloadKey, activeId, decisions, payload)
        }
        return true
      } catch (caught: unknown) {
        if (concurrentBatch && !concurrentBatch.settled) {
          for (const payloadKey of concurrentBatch.getPayloadKeys()) {
            deletePendingDecisionIfOwned(pendingDecisionsRef.current, payloadKey, concurrentBatch)
          }
          concurrentBatch.settled = true
          concurrentBatchesRef.current.delete(batchKey)
          concurrentBatch.reject(caught)
        }
        throw caught
      } finally {
        inFlightFlushKeysRef.current.delete(flushKey)
      }
    },
    [allInterruptPayloadsById, refreshLifecycle, rememberResolvedInterrupt, stream],
  )

  useEffect(() => {
    const active = new Set(activeGenerationById.values())
    for (const [key, coordinator] of coordinatorsRef.current) {
      if (!active.has(key)) {
        coordinator.cancel(new DOMException('Pending HiTL decisions were replaced', 'AbortError'))
        coordinatorsRef.current.delete(key)
      }
    }
    for (const [key, batch] of concurrentBatchesRef.current) {
      const batchPayloadKeys = batch.getPayloadKeys()
      if (batch.started) continue
      const activeKeys = [...active].sort()
      const batchKeysRemainActive = batchPayloadKeys.every((payloadKey) => active.has(payloadKey))
      const activeKeysRemainInBatch = activeKeys.every((payloadKey) =>
        batchPayloadKeys.includes(payloadKey),
      )
      const isSameBatch = activeKeys.length === batchPayloadKeys.length && batchKeysRemainActive
      if (isSameBatch) continue
      const isPureRemoval = activeKeysRemainInBatch
      const isPureExpansion = batchKeysRemainActive
      if (activeKeys.length > 0 && (isPureRemoval || isPureExpansion)) {
        const nextKey = concurrentInterruptBatchKey(generationRef.current, interruptPayloads)
        const existingBatch = concurrentBatchesRef.current.get(nextKey)
        if (existingBatch && existingBatch !== batch) {
          for (const payloadKey of batchPayloadKeys) {
            deletePendingDecisionIfOwned(pendingDecisionsRef.current, payloadKey, batch)
          }
          batch.reject(new DOMException('Pending HiTL decisions were replaced', 'AbortError'))
          concurrentBatchesRef.current.delete(key)
          if (!existingBatch.started) {
            for (const payloadKey of existingBatch.getPayloadKeys()) {
              deletePendingDecisionIfOwned(pendingDecisionsRef.current, payloadKey, existingBatch)
            }
            existingBatch.reject(
              new DOMException('Pending HiTL decisions were replaced', 'AbortError'),
            )
            concurrentBatchesRef.current.delete(nextKey)
          }
          continue
        }
        batch.replacePayloadKeys(activeKeys)
        concurrentBatchesRef.current.delete(key)
        concurrentBatchesRef.current.set(nextKey, batch)
        continue
      }
      for (const payloadKey of batchPayloadKeys) {
        deletePendingDecisionIfOwned(pendingDecisionsRef.current, payloadKey, batch)
      }
      batch.reject(new DOMException('Pending HiTL decisions were replaced', 'AbortError'))
      concurrentBatchesRef.current.delete(key)
    }
    for (const key of pendingDecisionsRef.current.keys()) {
      if (!active.has(key)) pendingDecisionsRef.current.delete(key)
    }
    void flushPendingDecisions(interruptPayloads).catch((caught: unknown) => {
      reportRuntimeFailure(caught, 'hitl_flush_failed')
    })
  }, [activeGenerationById, flushPendingDecisions, interruptPayloads])

  const firstInterruptId = interruptPayloads[0]?.interrupt_id ?? null
  const onResumeDecisions = useCallback(
    async (decisions: Decision[], _displayText?: string, interruptId?: string | null) => {
      const targetId = interruptId === undefined ? firstInterruptId : interruptId
      if (!targetId) {
        throw new DOMException('HiTL interrupt is no longer active', 'InvalidStateError')
      }
      const payload = allInterruptPayloadsById.get(targetId)
      if (!payload || !interruptPayloadsById.has(targetId)) {
        throw new DOMException('HiTL interrupt is no longer active', 'InvalidStateError')
      }
      const payloadKey = payloadGenerationKey(payload)
      if (activeGenerationByIdRef.current.get(targetId) !== payloadKey) {
        throw new DOMException('HiTL interrupt is no longer active', 'InvalidStateError')
      }
      if (interruptPayloads.length > 1) {
        const generation = generationRef.current
        const batchKey = concurrentInterruptBatchKey(generation, interruptPayloads)
        const payloadKeys = interruptPayloads.map(payloadGenerationKey).sort()
        const batch =
          concurrentBatchesRef.current.get(batchKey) ??
          createPendingConcurrentInterruptBatch(payloadKeys)
        concurrentBatchesRef.current.set(batchKey, batch)
        const pending = pendingDecisionsRef.current.get(payloadKey)
        if (!batch.started && pending?.owner !== batch) {
          pendingDecisionsRef.current.set(payloadKey, { decisions: [...decisions], owner: batch })
        }
        void flushPendingDecisions(interruptPayloads).catch((caught: unknown) => {
          reportRuntimeFailure(caught, 'hitl_flush_failed')
        })
        return batch.promise
      }
      const generation = generationRef.current
      const options = payload.namespace
        ? { interruptId: targetId, namespace: payload.namespace }
        : { interruptId: targetId }
      if (stream.hydrationPromise) await stream.hydrationPromise
      await stream.respond({ decisions }, options)
      try {
        await refreshLifecycle()
      } catch (caught: unknown) {
        reportRuntimeFailure(caught, 'hitl_refresh_failed')
        return
      }
      rememberResolvedInterrupt(generation, payloadKey, targetId, decisions, payload)
    },
    [
      allInterruptPayloadsById,
      firstInterruptId,
      flushPendingDecisions,
      interruptPayloads,
      interruptPayloadsById,
      refreshLifecycle,
      rememberResolvedInterrupt,
      stream,
    ],
  )

  const onResumeDecisionsRef = useRef(onResumeDecisions)
  useEffect(() => {
    onResumeDecisionsRef.current = onResumeDecisions
  }, [onResumeDecisions])

  const registerDecision = useCallback(
    async (
      actionIndex: number,
      decision: Decision,
      displayText?: string,
      interruptId?: string | null,
    ) => {
      const targetId = interruptId === undefined ? firstInterruptId : interruptId
      const payload = targetId ? interruptPayloadsById.get(targetId) : undefined
      if (!targetId || !payload) {
        throw new DOMException('HiTL interrupt is no longer active', 'InvalidStateError')
      }
      const payloadKey = payloadGenerationKey(payload)
      if (activeGenerationByIdRef.current.get(targetId) !== payloadKey) {
        throw new DOMException('HiTL interrupt is no longer active', 'InvalidStateError')
      }
      if (payload.action_requests.length <= 1) {
        await onResumeDecisions([decision], displayText, targetId)
        return
      }
      const coordinator =
        coordinatorsRef.current.get(payloadKey) ??
        createHiTLDecisionCoordinator({
          totalActions: payload.action_requests.length,
          interruptId: targetId,
          resume: (decisions, coordinatorDisplayText, coordinatorInterruptId) =>
            onResumeDecisionsRef.current(decisions, coordinatorDisplayText, coordinatorInterruptId),
        })
      coordinatorsRef.current.set(payloadKey, coordinator)
      await coordinator.registerDecision(actionIndex, decision, displayText)
    },
    [firstInterruptId, interruptPayloadsById, onResumeDecisions],
  )

  return { onResumeDecisions, registerDecision }
}

function payloadGenerationKey(payload: StandardInterruptPayload): string {
  return `${payload.interrupt_id}:${stableString({
    namespace: payload.namespace ?? [],
    actionRequests: payload.action_requests,
    reviewConfigs: payload.review_configs,
  })}`
}
