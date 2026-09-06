import type {
  ActionRequest,
  Decision,
  ReviewConfig,
  StandardInterruptPayload,
  ToolCallInfo,
} from '@/lib/types'
import { redactSensitiveRecord } from './sensitive-display'

interface ToolUiMetadata {
  approval_id: string
  allowed_decisions: ReviewConfig['allowed_decisions']
  hitl_interrupt_id: string
  hitl_action_index: number
  hitl_total_actions: number
  /** 스킬 빌더 AD-4 — "이 세션에서 계속 허용" 옵션 노출 (review_configs 플래그). */
  session_consent_eligible?: boolean
}

type ResumeDecisions = (
  decisions: Decision[],
  displayText?: string,
  interruptId?: string | null,
) => Promise<void>

const HITL_METADATA_KEYS = new Set([
  'approval_id',
  'allowed_decisions',
  'hitl_interrupt_id',
  'hitl_action_index',
  'hitl_total_actions',
  'session_consent_eligible',
])
function reviewForAction(
  action: ActionRequest,
  reviewConfigs: ReviewConfig[],
  index: number,
): ReviewConfig {
  return (
    reviewConfigs[index] ??
    reviewConfigs.find((config) => config.action_name === action.name) ?? {
      action_name: action.name,
      allowed_decisions: ['approve', 'reject'],
    }
  )
}

function metadataForAction(
  payload: StandardInterruptPayload,
  reviewConfig: ReviewConfig,
  index: number,
): ToolUiMetadata {
  const id = `${payload.interrupt_id}:${index}`
  return {
    approval_id: id,
    allowed_decisions: reviewConfig.allowed_decisions,
    hitl_interrupt_id: payload.interrupt_id,
    hitl_action_index: index,
    hitl_total_actions: payload.action_requests.length,
    ...(reviewConfig.session_consent_eligible ? { session_consent_eligible: true } : {}),
  }
}

function isAskUserRespondOnly(action: ActionRequest, reviewConfig: ReviewConfig): boolean {
  return (
    action.name === 'ask_user' &&
    reviewConfig.allowed_decisions.length === 1 &&
    reviewConfig.allowed_decisions[0] === 'respond'
  )
}

function stripHitLMetadata(args: Record<string, unknown>): Record<string, unknown> {
  return redactSensitiveRecord(
    Object.fromEntries(Object.entries(args).filter(([key]) => !HITL_METADATA_KEYS.has(key))),
  )
}

function equivalentToolArgs(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
): boolean {
  return JSON.stringify(stripHitLMetadata(left)) === JSON.stringify(stripHitLMetadata(right))
}

function containsToolArgs(
  superset: Record<string, unknown>,
  subset: Record<string, unknown>,
): boolean {
  const cleanedSuperset = stripHitLMetadata(superset)
  const cleanedSubset = stripHitLMetadata(subset)
  return Object.entries(cleanedSubset).every(
    ([key, value]) => JSON.stringify(cleanedSuperset[key] ?? null) === JSON.stringify(value),
  )
}

function isEmptyObject(value: Record<string, unknown>): boolean {
  return Object.keys(value).length === 0
}

function approvalTarget(synthetic: ToolCallInfo): {
  name: string
  args: Record<string, unknown>
} | null {
  if (synthetic.name !== 'request_approval') return null
  const toolName = synthetic.args.tool_name
  const toolArgs = synthetic.args.tool_args
  if (typeof toolName !== 'string') return null
  return {
    name: toolName,
    args:
      toolArgs && typeof toolArgs === 'object' && !Array.isArray(toolArgs)
        ? (toolArgs as Record<string, unknown>)
        : {},
  }
}

function withReplacementId(synthetic: ToolCallInfo, existing: ToolCallInfo): ToolCallInfo {
  const fallbackId = existing.id ?? synthetic.id
  if (!fallbackId) return synthetic
  return {
    ...synthetic,
    id: fallbackId,
    args: {
      ...synthetic.args,
      approval_id: fallbackId,
    },
  }
}

export function standardInterruptToToolCalls(payload: StandardInterruptPayload): ToolCallInfo[] {
  return payload.action_requests.map((action, index) => {
    const reviewConfig = reviewForAction(action, payload.review_configs, index)
    const metadata = metadataForAction(payload, reviewConfig, index)
    const safeArgs = redactSensitiveRecord(action.args)
    if (isAskUserRespondOnly(action, reviewConfig)) {
      return {
        id: metadata.approval_id,
        name: 'ask_user',
        args: {
          ...safeArgs,
          ...metadata,
        },
      }
    }

    return {
      id: metadata.approval_id,
      name: 'request_approval',
      args: {
        tool_name: action.name,
        tool_args: safeArgs,
        ...(action.description ? { description: action.description } : {}),
        ...metadata,
      },
    }
  })
}

export function mergeInterruptToolCalls(
  toolCalls: ToolCallInfo[],
  payload: StandardInterruptPayload,
): ToolCallInfo[] {
  const next = [...toolCalls]
  const syntheticToolCalls = standardInterruptToToolCalls(payload)
  const replacedIndices = new Set<number>()

  for (const synthetic of syntheticToolCalls) {
    let merged = false

    if (synthetic.name === 'ask_user') {
      for (let index = next.length - 1; index >= 0; index -= 1) {
        const existing = next[index]
        if (existing.name === synthetic.name && equivalentToolArgs(existing.args, synthetic.args)) {
          next[index] = {
            ...existing,
            args: { ...existing.args, ...synthetic.args },
          }
          merged = true
          break
        }
      }
    }

    const target = approvalTarget(synthetic)
    if (!merged && target) {
      for (let index = next.length - 1; index >= 0; index -= 1) {
        if (replacedIndices.has(index)) continue
        const existing = next[index]
        if (existing.name !== target.name) continue
        if (
          equivalentToolArgs(existing.args, target.args) ||
          containsToolArgs(target.args, existing.args) ||
          containsToolArgs(existing.args, target.args) ||
          isEmptyObject(existing.args) ||
          isEmptyObject(target.args)
        ) {
          next[index] = withReplacementId(synthetic, existing)
          replacedIndices.add(index)
          merged = true
          break
        }
      }
    }

    if (!merged) {
      next.push(synthetic)
    }
  }

  return next
}

export interface HiTLDecisionCoordinator {
  readonly interruptId: string | null
  cancel: (reason: unknown) => void
  registerDecision: (actionIndex: number, decision: Decision, displayText?: string) => Promise<void>
}

interface PendingDecisionBatch {
  readonly promise: Promise<void>
  readonly resolve: () => void
  readonly reject: (reason: unknown) => void
  started: boolean
}

function createPendingDecisionBatch(): PendingDecisionBatch {
  let resolve!: () => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject, started: false }
}

export function createHiTLDecisionCoordinator({
  totalActions,
  interruptId,
  resume,
}: {
  totalActions: number
  interruptId: string | null
  resume: ResumeDecisions
}): HiTLDecisionCoordinator {
  const decisions: Array<Decision | undefined> = Array.from({ length: totalActions })
  const displayTexts: Array<string | undefined> = Array.from({ length: totalActions })
  let resumed = false
  let resumeInFlight: Promise<void> | null = null
  let pendingBatch: PendingDecisionBatch | null = null
  let cancelled = false
  let cancellationReason: unknown = null

  const resumeOnce = (nextDecisions: Decision[], displayText?: string): Promise<void> => {
    if (resumed) return Promise.resolve()
    if (resumeInFlight) return resumeInFlight

    const attempt = Promise.resolve().then(() => resume(nextDecisions, displayText, interruptId))
    const trackedAttempt = attempt.then(() => {
      resumed = true
    })
    const cleanupAttempt = trackedAttempt.finally(() => {
      if (resumeInFlight === cleanupAttempt) resumeInFlight = null
    })
    resumeInFlight = cleanupAttempt
    return cleanupAttempt
  }

  return {
    interruptId,
    cancel(reason) {
      if (resumed || resumeInFlight || cancelled) return
      cancelled = true
      cancellationReason = reason
      decisions.fill(undefined)
      displayTexts.fill(undefined)
      const batch = pendingBatch
      pendingBatch = null
      batch?.reject(reason)
    },
    async registerDecision(actionIndex, decision, displayText) {
      if (resumed) return
      if (cancelled) throw cancellationReason
      if (actionIndex < 0 || actionIndex >= totalActions) {
        throw new RangeError(`HiTL action index ${actionIndex} is outside the pending batch`)
      }

      if (pendingBatch?.started) return pendingBatch.promise
      if (decisions[actionIndex] !== undefined && pendingBatch) return pendingBatch.promise

      decisions[actionIndex] = decision
      displayTexts[actionIndex] = displayText
      const batch = pendingBatch ?? createPendingDecisionBatch()
      pendingBatch = batch
      if (decisions.some((item) => item === undefined)) return batch.promise

      const combinedDisplayText = displayTexts.filter(Boolean).join(' | ') || undefined
      const combinedDecisions = decisions.filter((item): item is Decision => item !== undefined)
      batch.started = true
      void resumeOnce(combinedDecisions, combinedDisplayText).then(
        () => {
          if (pendingBatch === batch) pendingBatch = null
          batch.resolve()
        },
        (reason: unknown) => {
          decisions.fill(undefined)
          displayTexts.fill(undefined)
          if (pendingBatch === batch) pendingBatch = null
          batch.reject(reason)
        },
      )
      return batch.promise
    },
  }
}
