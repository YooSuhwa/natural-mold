import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'
import type { AgentSort, ConversationSort, NavigatorMode } from '@/lib/types'

/** 같은 탭에서 SSE 스트림이 진행 중일 때만 쓰는 로컬 오버레이 상태.
 *  서버 진실은 ``Conversation.active_run``(1초 폴링) — 이 값은 폴링이 따라잡기
 *  전의 즉시 반응용이며 다른 탭/백그라운드 run은 active_run으로만 표시된다. */
export type ConversationRuntimeStatus = 'idle' | 'running'

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
