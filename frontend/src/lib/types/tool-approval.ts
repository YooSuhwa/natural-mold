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
  /** The resource key settings and grants are saved under (`mcp:<id>`, `tool:<id>`, `builtin:<name>`). */
  key: string
  name: string
  /** The MCP server it comes from (tells same-named tools apart). */
  source?: string | null
  level: string
  /** The company decides it whatever the agent says. */
  locked: boolean
  grantable: boolean
  cells: ToolApprovalCell[]
}

export interface ToolApprovalView {
  settings: ToolApprovalPolicyV1 | null
  /** Saved per-tool entries that name no single tool of the agent. */
  unresolved?: string[]
  company_actions: Record<string, string>
  company_always_ask: string[]
  tools: ToolApprovalTool[]
}

export interface ToolGrant {
  id: string
  /** The tool's resource key (a tool name on grants made before keys). */
  tool_name: string
  created_at: string
}
