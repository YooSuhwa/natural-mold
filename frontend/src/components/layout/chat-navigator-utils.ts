import type { AgentSort, AgentSummary } from '@/lib/types'

export const RECENT_AGENT_CAP = 8
export const RECENT_SESSION_CAP = 10

export interface ChatRouteContext {
  agentId: string | null
  conversationId: string | null
}

export function parseChatRoute(pathname: string): ChatRouteContext {
  const match = /^\/agents\/([^/]+)(?:\/conversations\/([^/]+))?/.exec(pathname)
  return {
    agentId: match?.[1] ?? null,
    conversationId: match?.[2] ?? null,
  }
}

export function agentSortTime(agent: AgentSummary, sort: AgentSort): number {
  const value = sort === 'recent' ? (agent.last_used_at ?? agent.created_at) : agent.created_at
  const time = new Date(value).getTime()
  // 잘못된 날짜 문자열이 섞이면 NaN 비교가 정렬 전체를 불안정하게 만든다
  return Number.isNaN(time) ? 0 : time
}

export function matchesAgent(agent: AgentSummary, query: string): boolean {
  const normalized = query.toLowerCase()
  return (
    agent.name.toLowerCase().includes(normalized) ||
    (agent.description ?? '').toLowerCase().includes(normalized)
  )
}
