import type { Message, ToolCallInfo } from '@/lib/types'
import {
  parseSearchResults,
  sourceSummariesFromResults,
  type SearchSourceSummary,
} from '@/lib/chat/search-results'

export const TAVILY_SEARCH_TOOL_NAME = 'tavily_search'
export const DEEP_RESEARCH_SUMMARY_TOOL_NAME = 'deep_research_summary'

export interface DeepResearchSearchSummary {
  tool_call_id: string
  query: string
  result_count: number
  sources: SearchSourceSummary[]
}

export interface DeepResearchSummary {
  title: string
  total_count: number
  completed_count: number
  source_count: number
  domains: string[]
  searches: DeepResearchSearchSummary[]
  started_at: string | null
  completed_at: string | null
  duration_ms?: number
}

function toolCallId(call: ToolCallInfo, fallback: string): string {
  return call.id?.trim() || fallback
}

function queryFromArgs(args: Record<string, unknown>): string {
  const query = args.query ?? args.q
  return typeof query === 'string' && query.trim() ? query.trim() : 'Search'
}

function parseDate(value: string | null | undefined): number | null {
  if (!value) return null
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : null
}

function buildToolMessageByCallId(messages: readonly Message[]): Map<string, Message> {
  const byCallId = new Map<string, Message>()
  for (const message of messages) {
    if (message.role !== 'tool' || !message.tool_call_id) continue
    byCallId.set(message.tool_call_id, message)
  }
  return byCallId
}

function buildSummary({
  startedAt,
  previousUserContent,
  tavilyCalls,
  toolMessageByCallId,
}: {
  startedAt: string | null
  previousUserContent: string | null
  tavilyCalls: ToolCallInfo[]
  toolMessageByCallId: Map<string, Message>
}): DeepResearchSummary {
  const uniqueSources = new Map<string, SearchSourceSummary>()
  const domainCounts = new Map<string, number>()
  let completedCount = 0
  let latestCompletedAt: string | null = null

  const searches = tavilyCalls.map((call, index): DeepResearchSearchSummary => {
    const id = toolCallId(call, `tavily-${index}`)
    const toolMessage = toolMessageByCallId.get(id)
    const results = toolMessage ? parseSearchResults(toolMessage.content) : []
    const sources = sourceSummariesFromResults(results)
    if (toolMessage) {
      completedCount += 1
      const currentLatest = parseDate(latestCompletedAt)
      const nextCompleted = parseDate(toolMessage.created_at)
      if (currentLatest === null || (nextCompleted !== null && nextCompleted > currentLatest)) {
        latestCompletedAt = toolMessage.created_at
      }
    }
    for (const source of sources) {
      if (!uniqueSources.has(source.url)) uniqueSources.set(source.url, source)
      domainCounts.set(source.domain, (domainCounts.get(source.domain) ?? 0) + 1)
    }
    return {
      tool_call_id: id,
      query: queryFromArgs(call.args),
      result_count: results.length,
      sources: sources.slice(0, 3),
    }
  })

  const domains = [...domainCounts.entries()]
    .sort((left, right) => right[1] - left[1])
    .map(([domain]) => domain)

  const start = parseDate(startedAt)
  const end = parseDate(latestCompletedAt)
  const durationMs = start !== null && end !== null && end >= start ? end - start : undefined

  return {
    title: previousUserContent?.trim() || 'Deep Research',
    total_count: tavilyCalls.length,
    completed_count: completedCount,
    source_count: uniqueSources.size,
    domains,
    searches,
    started_at: startedAt,
    completed_at: completedCount === tavilyCalls.length ? latestCompletedAt : null,
    ...(durationMs !== undefined ? { duration_ms: durationMs } : {}),
  }
}

function replaceTavilyCallsWithSummary(
  calls: ToolCallInfo[],
  summaryCall: ToolCallInfo,
  insertSummary: boolean,
): ToolCallInfo[] {
  let inserted = !insertSummary
  const next: ToolCallInfo[] = []
  for (const call of calls) {
    if (call.name !== TAVILY_SEARCH_TOOL_NAME) {
      next.push(call)
      continue
    }
    if (!inserted) {
      next.push(summaryCall)
      inserted = true
    }
  }
  return next
}

function hasVisibleAssistantContent(message: Message): boolean {
  return Boolean(message.content.trim() || (message.tool_calls && message.tool_calls.length > 0))
}

function compactTurn({
  turn,
  previousUserContent,
  toolMessageByCallId,
}: {
  turn: Message[]
  previousUserContent: string | null
  toolMessageByCallId: Map<string, Message>
}): Message[] {
  const tavilyCalls: ToolCallInfo[] = []
  let firstTavilyAssistant: Message | null = null

  for (const message of turn) {
    if (message.role !== 'assistant' || !message.tool_calls) continue
    for (const call of message.tool_calls) {
      if (call.name !== TAVILY_SEARCH_TOOL_NAME) continue
      tavilyCalls.push(call)
      firstTavilyAssistant ??= message
    }
  }

  if (tavilyCalls.length <= 1 || firstTavilyAssistant === null) return turn

  const summary = buildSummary({
    startedAt: firstTavilyAssistant.created_at ?? null,
    previousUserContent,
    tavilyCalls,
    toolMessageByCallId,
  })
  const summaryToolCallId = `deep-research-${firstTavilyAssistant.id}`
  const suppressedToolCallIds = new Set(
    tavilyCalls.map((call, index) => toolCallId(call, `tavily-${index}`)),
  )
  const compacted: Message[] = []
  let summaryInserted = false

  for (const message of turn) {
    if (
      message.role === 'tool' &&
      message.tool_call_id &&
      suppressedToolCallIds.has(message.tool_call_id)
    ) {
      continue
    }

    if (message.role === 'assistant' && message.tool_calls) {
      const shouldInsertSummary = message.id === firstTavilyAssistant.id && !summaryInserted
      const nextMessage: Message = {
        ...message,
        tool_calls: replaceTavilyCallsWithSummary(
          message.tool_calls,
          {
            id: summaryToolCallId,
            name: DEEP_RESEARCH_SUMMARY_TOOL_NAME,
            args: summary as unknown as Record<string, unknown>,
          },
          shouldInsertSummary,
        ),
      }
      if (shouldInsertSummary) {
        summaryInserted = true
      }
      if (hasVisibleAssistantContent(nextMessage)) {
        compacted.push(nextMessage)
        if (
          shouldInsertSummary &&
          summary.completed_count === summary.total_count &&
          summary.total_count > 0
        ) {
          compacted.push({
            id: `deep-research-result-${firstTavilyAssistant.id}`,
            conversation_id: firstTavilyAssistant.conversation_id,
            role: 'tool',
            content: JSON.stringify(summary),
            tool_calls: null,
            tool_call_id: summaryToolCallId,
            created_at: summary.completed_at ?? firstTavilyAssistant.created_at,
          })
        }
      }
      continue
    }

    compacted.push(message)
  }

  return compacted
}

export function compactDeepResearchMessages(messages: readonly Message[]): Message[] {
  const toolMessageByCallId = buildToolMessageByCallId(messages)
  const compacted: Message[] = []
  let previousUserContent: string | null = null
  let turn: Message[] = []

  const flushTurn = () => {
    if (turn.length === 0) return
    compacted.push(...compactTurn({ turn, previousUserContent, toolMessageByCallId }))
    turn = []
  }

  for (const message of messages) {
    if (message.role === 'user') {
      flushTurn()
      previousUserContent = message.content
      compacted.push(message)
      continue
    }
    turn.push(message)
  }
  flushTurn()

  return compacted
}
