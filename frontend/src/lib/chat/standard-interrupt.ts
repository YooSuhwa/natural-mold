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
  }
}

function isAskUserRespondOnly(action: ActionRequest, reviewConfig: ReviewConfig): boolean {
  return (
    action.name === 'ask_user' &&
    reviewConfig.allowed_decisions.length === 1 &&
    reviewConfig.allowed_decisions[0] === 'respond'
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
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
  registerDecision: (actionIndex: number, decision: Decision, displayText?: string) => Promise<void>
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

  return {
    async registerDecision(actionIndex, decision, displayText) {
      if (resumed) return
      if (actionIndex < 0 || actionIndex >= totalActions) {
        resumed = true
        await resume([decision], displayText, interruptId)
        return
      }

      decisions[actionIndex] = decision
      displayTexts[actionIndex] = displayText
      if (decisions.some((item) => item === undefined)) return

      resumed = true
      const combinedDisplayText = displayTexts.filter(Boolean).join(' | ') || undefined
      await resume(decisions as Decision[], combinedDisplayText, interruptId)
    },
  }
}
