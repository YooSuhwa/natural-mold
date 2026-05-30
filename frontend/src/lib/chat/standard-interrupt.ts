import type {
  ActionRequest,
  Decision,
  ReviewConfig,
  StandardInterruptPayload,
  ToolCallInfo,
} from '@/lib/types'

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

export function standardInterruptToToolCalls(
  payload: StandardInterruptPayload,
): ToolCallInfo[] {
  return payload.action_requests.map((action, index) => {
    const reviewConfig = reviewForAction(action, payload.review_configs, index)
    const metadata = metadataForAction(payload, reviewConfig, index)
    if (isAskUserRespondOnly(action, reviewConfig)) {
      return {
        id: metadata.approval_id,
        name: 'ask_user',
        args: {
          ...action.args,
          ...metadata,
        },
      }
    }

    return {
      id: metadata.approval_id,
      name: 'request_approval',
      args: {
        tool_name: action.name,
        tool_args: action.args,
        ...(action.description ? { description: action.description } : {}),
        ...metadata,
      },
    }
  })
}

export interface HiTLDecisionCoordinator {
  registerDecision: (
    actionIndex: number,
    decision: Decision,
    displayText?: string,
  ) => Promise<void>
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
