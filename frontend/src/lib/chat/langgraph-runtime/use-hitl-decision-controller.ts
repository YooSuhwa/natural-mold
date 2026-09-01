'use client'

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { resolvedInterruptToolCallsFromDecisions } from './hitl-interrupts'
import { stableString } from './message-list'
import { createHiTLDecisionCoordinator, type HiTLDecisionCoordinator } from '../standard-interrupt'
import type { Decision, StandardInterruptPayload } from '@/lib/types'
import type { ResolvedInterruptToolCall } from './hitl-interrupts'
import { reportRuntimeFailure } from './runtime-warning'

interface HitlStreamActions {
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
  const pendingDecisionsRef = useRef(new Map<string, Decision[]>())
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
    coordinatorsRef.current.clear()
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
      const decisionsById = new Map<string, Decision[]>()
      const generationKeyById = new Map<string, string>()
      for (const payload of activePayloads) {
        const activeId = payload.interrupt_id
        const generationKey = payloadGenerationKey(payload)
        const pending = pendingDecisionsRef.current.get(generationKey)
        if (!pending) return false
        decisionsById.set(activeId, pending)
        generationKeyById.set(activeId, generationKey)
      }

      const generation = generationRef.current
      const flushKey = `${generation}:${activePayloads
        .map((payload) => payloadGenerationKey(payload))
        .join('|')}`
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
          await stream.respond({ decisions }, options)
          await refreshLifecycle()
          if (generationRef.current !== generation) return true
          if (activeGenerationByIdRef.current.get(activeId) !== payloadKey) return true
          pendingDecisionsRef.current.delete(payloadKey)
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
        await stream.respondAll(responsesById)
        await refreshLifecycle()
        if (generationRef.current !== generation) return true
        for (const [activeId, decisions] of decisionsById) {
          const payload = payloadsAtDecision.get(activeId)
          const payloadKey = generationKeyById.get(activeId)
          if (!payload || !payloadKey) continue
          if (activeGenerationByIdRef.current.get(activeId) !== payloadKey) continue
          pendingDecisionsRef.current.delete(payloadKey)
          rememberResolvedInterrupt(generation, payloadKey, activeId, decisions, payload)
        }
        return true
      } finally {
        inFlightFlushKeysRef.current.delete(flushKey)
      }
    },
    [allInterruptPayloadsById, refreshLifecycle, rememberResolvedInterrupt, stream],
  )

  useEffect(() => {
    const active = new Set(activeGenerationById.values())
    for (const key of coordinatorsRef.current.keys()) {
      if (!active.has(key)) coordinatorsRef.current.delete(key)
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
      const targetId = interruptId ?? firstInterruptId
      if (!targetId) return
      const payload = allInterruptPayloadsById.get(targetId)
      if (!payload || !interruptPayloadsById.has(targetId)) return
      const payloadKey = payloadGenerationKey(payload)
      if (activeGenerationByIdRef.current.get(targetId) !== payloadKey) return
      if (interruptPayloads.length > 1) {
        pendingDecisionsRef.current.set(payloadKey, [...decisions])
        await flushPendingDecisions(interruptPayloads)
        return
      }
      const generation = generationRef.current
      const options = payload.namespace
        ? { interruptId: targetId, namespace: payload.namespace }
        : { interruptId: targetId }
      await stream.respond({ decisions }, options)
      await refreshLifecycle()
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
      const targetId = interruptId ?? firstInterruptId
      const payload = targetId ? interruptPayloadsById.get(targetId) : undefined
      if (!targetId || !payload) return
      const payloadKey = payloadGenerationKey(payload)
      if (activeGenerationByIdRef.current.get(targetId) !== payloadKey) return
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
