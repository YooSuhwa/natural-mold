'use client'

import { useMemo, useState, type KeyboardEvent } from 'react'
import { toast } from 'sonner'
import { Activity, ChevronRightIcon, Download, Plus, Server, Upload } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { announceHealthResult } from '@/lib/health-check-toast'

import { Button } from '@/components/ui/button'
import { StatusChip } from '@/components/shared/status-chip'
import { EmptyState } from '@/components/shared/empty-state'
import { SearchInput } from '@/components/shared/search-input'
import {
  CountedLineTabs,
  ResourceGrid,
  ResourcePage,
  ResourcePanel,
  ResourceToolbar,
} from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'
import { McpServerWizard } from '@/components/mcp/mcp-server-wizard'
import { McpServerDetailDialog } from '@/components/mcp/mcp-server-detail-dialog'
import { McpImportDialog } from '@/components/mcp/mcp-import-dialog'
import { useExportMcpServers, useMcpServers } from '@/lib/hooks/use-mcp-servers'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import type { McpServer } from '@/lib/types/mcp'
import type { HealthCheckEntry } from '@/lib/types/health'
import { cn } from '@/lib/utils'

type McpStatusTab = 'all' | 'connected' | 'auth_needed' | 'unreachable' | 'disabled' | 'unknown'

const ALL_TAB = 'all'
const MCP_STATUS_TABS: McpStatusTab[] = [
  ALL_TAB,
  'connected',
  'auth_needed',
  'unreachable',
  'disabled',
  'unknown',
]

function formatDate(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleDateString()
}

function normalizeStatus(value: string | null | undefined): Exclude<McpStatusTab, 'all'> {
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

export default function McpServersPage() {
  const t = useTranslations('mcp.page')
  const { data: servers, isLoading } = useMcpServers()
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<McpStatusTab>(ALL_TAB)
  const [search, setSearch] = useState('')
  const exportMutation = useExportMcpServers()

  async function handleExport() {
    try {
      const data = await exportMutation.mutateAsync()
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `mcp-servers-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      const count = Object.keys(data.mcpServers).length
      toast.success(t('toast.exported', { count }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.exportFailed'))
    }
  }

  // Latest health probe per server. Falls back to the server's own
  // `status` field when no probe row exists yet.
  const healthByServer = useMemo(() => {
    const map = new Map<string, HealthCheckEntry>()
    ;(healthEntries ?? []).forEach((h) => map.set(h.target_id, h))
    return map
  }, [healthEntries])

  async function handleCheckNow(serverId: string) {
    try {
      const result = await runHealthCheck.mutateAsync({
        targetKind: 'mcp_server',
        targetId: serverId,
      })
      announceHealthResult(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.healthCheckFailed'))
    }
  }

  const data = useMemo(() => servers ?? [], [servers])
  const normalizedSearch = search.trim().toLowerCase()

  const filteredServers = useMemo(() => {
    return data.filter((server) => {
      const status = normalizeStatus(server.status)
      if (activeTab !== ALL_TAB && status !== activeTab) return false
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
    })
  }, [activeTab, data, normalizedSearch])

  function countServers(tab: McpStatusTab): number {
    return data.filter((server) => {
      const status = normalizeStatus(server.status)
      if (tab !== ALL_TAB && status !== tab) return false
      if (!normalizedSearch) return true
      return [server.name, server.description, server.transport, server.command, server.url]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedSearch))
    }).length
  }

  const tabs = MCP_STATUS_TABS.map((value) => ({
    value,
    label: t(`tabs.${value}`),
    countLabel: t('count', { count: countServers(value) }),
  }))

  const isInitialEmpty = !isLoading && data.length === 0
  const isFilteredEmpty = !isLoading && data.length > 0 && filteredServers.length === 0

  return (
    <ResourcePage
      title={t('title')}
      description={t('description')}
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={() => setImportOpen(true)}>
            <Download className="size-4" />
            {t('actions.import')}
          </Button>
          <Button variant="outline" onClick={handleExport} disabled={exportMutation.isPending}>
            <Upload className="size-4" />
            {t('actions.export')}
          </Button>
          <Button onClick={() => setWizardOpen(true)}>
            <Plus className="size-4" />
            {t('actions.new')}
          </Button>
        </div>
      }
    >
      <ResourcePanel>
        {isInitialEmpty ? (
          <ResourcePanel.Body>
            <EmptyState
              icon={<Server className="size-6" />}
              title={t('empty.title')}
              description={t('empty.description')}
              className="bg-card/50"
              action={
                <Button onClick={() => setWizardOpen(true)}>
                  <Plus className="size-4" />
                  {t('empty.action')}
                </Button>
              }
            />
          </ResourcePanel.Body>
        ) : (
          <>
            <ResourcePanel.Toolbar>
              <CountedLineTabs
                ariaLabel={t('tabs.label')}
                value={activeTab}
                tabs={tabs}
                onValueChange={(value) => setActiveTab(value as McpStatusTab)}
              />
              <ResourceToolbar>
                <SearchInput
                  containerClassName="flex-1 sm:max-w-[360px]"
                  placeholder={t('searchPlaceholder')}
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </ResourceToolbar>
            </ResourcePanel.Toolbar>

            <ResourcePanel.Body className="bg-background/30">
              {isLoading ? (
                <ResourceGrid minColumnWidth={252}>
                  {Array.from({ length: 6 }).map((_, index) => (
                    <Skeleton key={index} className="h-[188px] rounded-md" />
                  ))}
                </ResourceGrid>
              ) : isFilteredEmpty ? (
                <EmptyState title={t('empty.filtered')} className="bg-card/50" />
              ) : (
                <ResourceGrid minColumnWidth={252}>
                  {filteredServers.map((server) => (
                    <McpServerCard
                      key={server.id}
                      server={server}
                      healthEntry={healthByServer.get(server.id)}
                      toolCountLabel={t('toolCount', {
                        count: server.last_tool_count ?? 0,
                      })}
                      endpointLabel={formatEndpoint(server)}
                      lastResponseLabel={formatDate(server.last_pinged_at)}
                      checkedAtLabel={
                        healthByServer.get(server.id)
                          ? formatRelativeTime(healthByServer.get(server.id)!.checked_at, t)
                          : null
                      }
                      checkNowLabel={t('actions.checkNow')}
                      checkNowAriaLabel={t('actions.checkNowFor', { name: server.name })}
                      manageLabel={t('actions.manage')}
                      onOpen={setDetailId}
                      onCheckNow={handleCheckNow}
                      checking={runHealthCheck.isPending}
                    />
                  ))}
                </ResourceGrid>
              )}
            </ResourcePanel.Body>
          </>
        )}
      </ResourcePanel>

      <McpServerWizard open={wizardOpen} onOpenChange={setWizardOpen} />
      <McpImportDialog open={importOpen} onOpenChange={setImportOpen} />
      <McpServerDetailDialog
        serverId={detailId}
        open={!!detailId}
        onOpenChange={(open) => !open && setDetailId(null)}
      />
    </ResourcePage>
  )
}

function McpServerCard({
  server,
  healthEntry,
  toolCountLabel,
  endpointLabel,
  lastResponseLabel,
  checkedAtLabel,
  checkNowLabel,
  checkNowAriaLabel,
  manageLabel,
  onOpen,
  onCheckNow,
  checking,
}: {
  server: McpServer
  healthEntry: HealthCheckEntry | undefined
  toolCountLabel: string
  endpointLabel: string
  lastResponseLabel: string
  checkedAtLabel: string | null
  checkNowLabel: string
  checkNowAriaLabel: string
  manageLabel: string
  onOpen: (id: string) => void
  onCheckNow: (id: string) => void
  checking: boolean
}) {
  const tone = pickMcpCardTone(`${server.transport}:${server.name}:${server.status}`)

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onOpen(server.id)
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${server.name} ${toolCountLabel}`}
      onClick={() => onOpen(server.id)}
      onKeyDown={handleKeyDown}
      className={cn(mcpCardClassName(tone))}
    >
      <div className="flex items-start justify-between gap-3">
        <span
          className={cn(
            'inline-flex size-9 shrink-0 items-center justify-center rounded-lg',
            tone.icon,
          )}
        >
          <Server className="size-4.5" />
        </span>
        <span
          className={cn(
            'inline-flex min-w-0 max-w-[132px] items-center gap-1 rounded-md border px-2 py-1 text-[11px] font-semibold leading-none',
            tone.badge,
          )}
        >
          <span className={cn('size-1.5 shrink-0 rounded-full', tone.dot)} />
          <span className="truncate">{server.transport}</span>
        </span>
      </div>

      <span className="mt-3 line-clamp-1 text-[15px] font-bold leading-tight text-foreground">
        {server.name}
      </span>
      <p className="mt-2 line-clamp-2 min-h-[2.65em] text-xs leading-[1.45] text-muted-foreground">
        {server.description ?? endpointLabel}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <StatusChip
          variant={healthEntry?.status ?? server.status}
          className="max-w-[128px] bg-white/55 text-[10.5px] shadow-sm ring-white/80 dark:bg-white/10 dark:ring-white/10"
        />
        <span className={mcpMetaClassName}>{toolCountLabel}</span>
        {lastResponseLabel ? <span className={mcpMetaClassName}>{lastResponseLabel}</span> : null}
      </div>
      <p className="mt-2 truncate font-mono text-[11px] text-muted-foreground/80">
        {endpointLabel}
      </p>
      {checkedAtLabel ? (
        <p className="mt-1 text-[11px] font-medium text-muted-foreground">{checkedAtLabel}</p>
      ) : null}

      <div className="mt-auto flex items-center justify-between gap-2 pt-3">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label={checkNowAriaLabel}
          data-testid={`check-now-${server.id}`}
          className="h-7 px-2 text-xs"
          onClick={(event) => {
            event.stopPropagation()
            onCheckNow(server.id)
          }}
          disabled={checking}
        >
          <Activity className="size-3.5" />
          {checkNowLabel}
        </Button>
        <span
          className={cn(
            'inline-flex items-center gap-0.5 text-xs font-semibold text-muted-foreground transition-all duration-150',
            'group-hover:translate-x-0.5 group-hover:text-[var(--primary-strong)]',
            'group-focus-visible:translate-x-0.5 group-focus-visible:text-[var(--primary-strong)]',
          )}
        >
          {manageLabel}
          <ChevronRightIcon aria-hidden className="size-3" />
        </span>
      </div>
    </div>
  )
}

function formatRelativeTime(iso: string, t: ReturnType<typeof useTranslations>): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const deltaSec = Math.floor((Date.now() - then) / 1000)
  if (deltaSec < 60) return t('relative.secondsAgo', { count: deltaSec })
  if (deltaSec < 3600) return t('relative.minutesAgo', { count: Math.floor(deltaSec / 60) })
  if (deltaSec < 86400) return t('relative.hoursAgo', { count: Math.floor(deltaSec / 3600) })
  return t('relative.daysAgo', { count: Math.floor(deltaSec / 86400) })
}

function formatEndpoint(server: McpServer): string {
  if (server.transport === 'stdio') return server.command ?? 'stdio'
  return server.url ?? server.transport
}

type McpCardTone = {
  card: string
  icon: string
  badge: string
  dot: string
}

const MCP_CARD_TONES: McpCardTone[] = [
  {
    card: 'bg-violet-50/75 hover:border-violet-200 dark:bg-violet-500/10 dark:hover:border-violet-400/30',
    icon: 'bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-200',
    badge:
      'border-violet-100 bg-white/70 text-violet-800 dark:border-violet-400/20 dark:bg-violet-500/10 dark:text-violet-200',
    dot: 'bg-violet-500',
  },
  {
    card: 'bg-sky-50/75 hover:border-sky-200 dark:bg-sky-500/10 dark:hover:border-sky-400/30',
    icon: 'bg-sky-100 text-sky-700 dark:bg-sky-500/20 dark:text-sky-200',
    badge:
      'border-sky-100 bg-white/70 text-sky-800 dark:border-sky-400/20 dark:bg-sky-500/10 dark:text-sky-200',
    dot: 'bg-sky-500',
  },
  {
    card: 'bg-emerald-50/75 hover:border-emerald-200 dark:bg-emerald-500/10 dark:hover:border-emerald-400/30',
    icon: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-200',
    badge:
      'border-emerald-100 bg-white/70 text-emerald-800 dark:border-emerald-400/20 dark:bg-emerald-500/10 dark:text-emerald-200',
    dot: 'bg-emerald-500',
  },
  {
    card: 'bg-amber-50/75 hover:border-amber-200 dark:bg-amber-500/10 dark:hover:border-amber-400/30',
    icon: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-200',
    badge:
      'border-amber-100 bg-white/70 text-amber-800 dark:border-amber-400/20 dark:bg-amber-500/10 dark:text-amber-200',
    dot: 'bg-amber-500',
  },
  {
    card: 'bg-rose-50/75 hover:border-rose-200 dark:bg-rose-500/10 dark:hover:border-rose-400/30',
    icon: 'bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-200',
    badge:
      'border-rose-100 bg-white/70 text-rose-800 dark:border-rose-400/20 dark:bg-rose-500/10 dark:text-rose-200',
    dot: 'bg-rose-500',
  },
]

const mcpMetaClassName =
  'inline-flex max-w-[140px] items-center rounded border border-white/80 bg-white/55 px-1.5 py-0.5 text-[10.5px] font-semibold leading-none text-foreground shadow-sm dark:border-white/10 dark:bg-white/10'

function mcpCardClassName(tone: McpCardTone): string {
  return cn(
    'group relative flex min-h-[198px] cursor-pointer flex-col rounded-md border border-transparent p-4 text-left',
    'shadow-[0_10px_24px_-22px_rgba(15,23,42,0.45)] transition-all duration-150',
    'hover:-translate-y-px hover:shadow-[0_18px_32px_-24px_rgba(15,23,42,0.55)]',
    'focus-visible:-translate-y-px focus-visible:border-emerald-300 focus-visible:shadow-md',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40',
    tone.card,
  )
}

function pickMcpCardTone(seed: string): McpCardTone {
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) hash += seed.charCodeAt(i)
  return MCP_CARD_TONES[hash % MCP_CARD_TONES.length]
}
