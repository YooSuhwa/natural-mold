import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'
import type {
  AgentSort,
  ConversationRuntimeStatus,
  ConversationSort,
  NavigatorMode,
} from '@/lib/types'

export const navigatorModeAtom = atomWithStorage<NavigatorMode>(
  'moldy.chatNavigator.mode',
  'agent_grouped',
)
export const agentSortAtom = atomWithStorage<AgentSort>('moldy.chatNavigator.agentSort', 'recent')
export const sessionSortAtom = atomWithStorage<ConversationSort>(
  'moldy.chatNavigator.sessionSort',
  'updated',
)
export const singleExpandedAgentAtom = atomWithStorage(
  'moldy.chatNavigator.singleExpandedAgent',
  false,
)
export const expandedAgentIdsAtom = atomWithStorage<string[]>(
  'moldy.chatNavigator.expandedAgentIds',
  [],
)
/** 활성 에이전트는 기본 펼침 — 사용자가 명시적으로 접은 경우만 여기에 기록한다. */
export const collapsedAgentIdsAtom = atomWithStorage<string[]>(
  'moldy.chatNavigator.collapsedAgentIds',
  [],
)
export const expandedListScopesAtom = atomWithStorage<string[]>(
  'moldy.chatNavigator.expandedListScopes',
  [],
)
export const shortcutPreviewActiveAtom = atom(false)
export const conversationRuntimeStatusAtom = atom<Record<string, ConversationRuntimeStatus>>({})
