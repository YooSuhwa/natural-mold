import { atom } from 'jotai'
import { clampPanelWidth, createStoredPanelWidthAtom } from './panel-width'

export const RIGHT_RAIL_WIDTH_DEFAULT_PX = 384
export const RIGHT_RAIL_WIDTH_MIN_PX = 320
export const RIGHT_RAIL_WIDTH_MAX_PX = 720
export const RIGHT_RAIL_COLLAPSE_THRESHOLD_PX = 280

const CHAT_MIN_WIDTH_PX = 520

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

function artifactView(payload: ArtifactsPayload): 'list' | 'preview' {
  return payload.view ?? (payload.selectedArtifactId ? 'preview' : 'list')
}

export function isArtifactPreviewOpen(
  state: RightRailState,
  conversationId: string,
  artifactId: string,
): boolean {
  return (
    state.mode === 'artifacts' &&
    state.artifacts.conversationId === conversationId &&
    artifactView(state.artifacts) === 'preview' &&
    state.artifacts.selectedArtifactId === artifactId
  )
}

export function isArtifactListOpen(state: RightRailState, conversationId: string): boolean {
  return (
    state.mode === 'artifacts' &&
    state.artifacts.conversationId === conversationId &&
    artifactView(state.artifacts) === 'list'
  )
}

export function toggleArtifactPreviewRailState(
  state: RightRailState,
  payload: {
    conversationId: string
    artifactId: string
  },
): RightRailState {
  if (isArtifactPreviewOpen(state, payload.conversationId, payload.artifactId)) {
    return { mode: 'none' }
  }

  return {
    mode: 'artifacts',
    artifacts: {
      conversationId: payload.conversationId,
      selectedArtifactId: payload.artifactId,
      view: 'preview',
    },
  }
}

export function toggleArtifactListRailState(
  state: RightRailState,
  conversationId: string,
): RightRailState {
  if (isArtifactListOpen(state, conversationId)) return { mode: 'none' }

  return {
    mode: 'artifacts',
    artifacts: { conversationId, view: 'list' },
  }
}

export function clampRightRailWidth(width: number, viewportWidth?: number): number {
  const resolvedViewportWidth =
    viewportWidth ?? (typeof window === 'undefined' ? Number.POSITIVE_INFINITY : window.innerWidth)
  const viewportMax = Math.min(RIGHT_RAIL_WIDTH_MAX_PX, resolvedViewportWidth - CHAT_MIN_WIDTH_PX)
  const max = Math.max(RIGHT_RAIL_WIDTH_MIN_PX, viewportMax)

  return clampPanelWidth(width, {
    min: RIGHT_RAIL_WIDTH_MIN_PX,
    max,
  })
}

export const chatRightRailAtom = atom<RightRailState>({ mode: 'none' })

export const chatRightRailWidthAtom = createStoredPanelWidthAtom('moldy.chatRightRail.widthPx', {
  defaultWidth: RIGHT_RAIL_WIDTH_DEFAULT_PX,
  minWidth: RIGHT_RAIL_WIDTH_MIN_PX,
  maxWidth: RIGHT_RAIL_WIDTH_MAX_PX,
})
