import { atom } from 'jotai'

export type RightRailMode = 'none' | 'subagent' | 'tool-result' | 'outline'

export interface SubagentPayload {
  toolCallId: string
  agentName: string
  input?: string
}

export interface ToolResultPayload {
  toolCallId: string
  toolName: string
  args?: unknown
  result?: unknown
  status?: 'running' | 'complete' | 'incomplete'
}

export interface OutlinePayload {
  messageId: string
  content: string
}

export type RightRailState =
  | { mode: 'none' }
  | { mode: 'subagent'; subagent: SubagentPayload }
  | { mode: 'tool-result'; toolResult: ToolResultPayload }
  | { mode: 'outline'; outline: OutlinePayload }

export const chatRightRailAtom = atom<RightRailState>({ mode: 'none' })
