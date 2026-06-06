import { atom } from 'jotai'

export type RightRailMode = 'none' | 'subagent' | 'tool-result' | 'outline' | 'artifacts'

export interface SubagentPayload {
  conversationId?: string | null
  toolCallId: string
  agentName: string
  input?: string
}

export interface ToolResultPayload {
  conversationId?: string | null
  toolCallId: string
  toolName: string
  args?: unknown
  result?: unknown
  status?: 'running' | 'complete' | 'incomplete'
}

export interface OutlinePayload {
  conversationId?: string | null
  messageId: string
  content: string
}

export interface ArtifactsPayload {
  conversationId: string
  selectedArtifactId?: string | null
  view?: 'list' | 'preview'
  previewMode?: 'preview' | 'code'
}

export type RightRailState =
  | { mode: 'none' }
  | { mode: 'subagent'; subagent: SubagentPayload }
  | { mode: 'tool-result'; toolResult: ToolResultPayload }
  | { mode: 'outline'; outline: OutlinePayload }
  | { mode: 'artifacts'; artifacts: ArtifactsPayload }

export const chatRightRailAtom = atom<RightRailState>({ mode: 'none' })
