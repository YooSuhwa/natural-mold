/** Tool approval API shapes (approval-modes-plan 3.5). */
import type { ToolApprovalPath, ToolApprovalPolicyV1, ToolApprovalRule } from './runtime-policy'

export type ToolApprovalKind = 'ask' | 'auto' | 'when' | 'hidden'
export type ToolApprovalDelivery = 'card' | 'wait' | 'per_schedule' | 'per_deployment'

export interface ToolApprovalCell {
  path: ToolApprovalPath
  kind: ToolApprovalKind
  layer: string
  reason: string
  delivery: ToolApprovalDelivery
  rule: ToolApprovalRule | null
}

export interface ToolApprovalTool {
  name: string
  level: string
  /** The company decides it whatever the agent says. */
  locked: boolean
  grantable: boolean
  cells: ToolApprovalCell[]
}

export interface ToolApprovalView {
  settings: ToolApprovalPolicyV1 | null
  company_actions: Record<string, string>
  company_always_ask: string[]
  tools: ToolApprovalTool[]
}

export interface ToolGrant {
  id: string
  tool_name: string
  created_at: string
}
