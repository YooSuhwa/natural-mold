import type { useTranslations } from 'next-intl'

import type { McpServer } from '@/lib/types/mcp'

export type McpStatusTab =
  | 'all'
  | 'connected'
  | 'auth_needed'
  | 'unreachable'
  | 'disabled'
  | 'unknown'

export const ALL_MCP_STATUS_TAB = 'all'

export const MCP_STATUS_TABS: readonly McpStatusTab[] = [
  ALL_MCP_STATUS_TAB,
  'connected',
  'auth_needed',
  'unreachable',
  'disabled',
  'unknown',
]

export function formatMcpDate(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleDateString()
}

export function normalizeMcpStatus(value: string | null | undefined): Exclude<McpStatusTab, 'all'> {
  if (
    value === 'connected' ||
    value === 'auth_needed' ||
    value === 'unreachable' ||
    value === 'disabled' ||
    value === 'unknown'
  ) {
    return value
  }
  return 'unknown'
}

export function formatMcpRelativeTime(iso: string, t: ReturnType<typeof useTranslations>): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const deltaSec = Math.floor((Date.now() - then) / 1000)
  if (deltaSec < 60) return t('relative.secondsAgo', { count: deltaSec })
  if (deltaSec < 3600) return t('relative.minutesAgo', { count: Math.floor(deltaSec / 60) })
  if (deltaSec < 86400) return t('relative.hoursAgo', { count: Math.floor(deltaSec / 3600) })
  return t('relative.daysAgo', { count: Math.floor(deltaSec / 86400) })
}

export function formatMcpEndpoint(server: McpServer): string {
  if (server.transport === 'stdio') return server.command ?? 'stdio'
  return server.url ?? server.transport
}

export function matchesMcpServerSearch(server: McpServer, normalizedSearch: string): boolean {
  if (!normalizedSearch) return true
  return [
    server.name,
    server.description,
    server.transport,
    server.command,
    server.url,
    server.status,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(normalizedSearch))
}
