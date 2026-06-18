'use client'

import { useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { toast } from 'sonner'
import { Download, Plus, Server, Upload } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { announceHealthResult } from '@/lib/health-check-toast'

import { Button } from '@/components/ui/button'
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
import { PublishWizard } from '@/components/marketplace/publish-wizard'
import { useExportMcpServers, useMcpServers } from '@/lib/hooks/use-mcp-servers'
import { useMcpHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import type { McpServer } from '@/lib/types/mcp'
import type { HealthCheckEntry } from '@/lib/types/health'
import { McpImportDialog } from './mcp-import-dialog'
import { McpServerCard } from './mcp-server-card'
import { McpServerDetailDialog } from './mcp-server-detail-dialog'
import {
  ALL_MCP_STATUS_TAB,
  MCP_STATUS_TABS,
  formatMcpDate,
  formatMcpEndpoint,
  formatMcpRelativeTime,
  matchesMcpServerSearch,
  normalizeMcpStatus,
  type McpStatusTab,
} from './mcp-server-page-utils'
import { McpServerWizard } from './mcp-server-wizard'

export function McpServersPageClient() {
  const t = useTranslations('mcp.page')
  const router = useRouter()
  const searchParams = useSearchParams()
  const deepLinkDetailId = searchParams.get('detailId')
  const { data: servers, isLoading } = useMcpServers()
  const { data: healthEntries } = useMcpHealth()
  const runHealthCheck = useRunHealthCheck()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  // Sentinel for the detail dialog source of truth:
  //   undefined → user has not touched a card; the deep-link URL drives it
  //   null      → user explicitly closed the dialog (deep link is ignored)
  //   string    → user opened a card manually (overrides the deep link)
  // Once the user interacts, manual state wins so opening card Y after a
  // `?detailId=X` deep link cannot flicker back to X, and closing cannot
  // momentarily re-fall back to the deep-link id.
  const [manualDetailId, setManualDetailId] = useState<string | null | undefined>(undefined)
  const detailId = manualDetailId === undefined ? deepLinkDetailId : manualDetailId
  const [publishServer, setPublishServer] = useState<McpServer | null>(null)
  const [activeTab, setActiveTab] = useState<McpStatusTab>(ALL_MCP_STATUS_TAB)
  const [search, setSearch] = useState('')
  const exportMutation = useExportMcpServers()

  function openDetail(id: string) {
    setManualDetailId(id)
    // Keep the URL aligned so a refresh restores the card the user is actually
    // viewing rather than the original deep-link target.
    if (deepLinkDetailId && deepLinkDetailId !== id) {
      router.replace(`/mcp-servers?detailId=${id}`)
    }
  }

  function closeDetail() {
    setManualDetailId(null)
    if (deepLinkDetailId) router.replace('/mcp-servers')
  }

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
      const status = normalizeMcpStatus(server.status)
      if (activeTab !== ALL_MCP_STATUS_TAB && status !== activeTab) return false
      return matchesMcpServerSearch(server, normalizedSearch)
    })
  }, [activeTab, data, normalizedSearch])

  function countServers(tab: McpStatusTab): number {
    return data.filter((server) => {
      const status = normalizeMcpStatus(server.status)
      if (tab !== ALL_MCP_STATUS_TAB && status !== tab) return false
      return matchesMcpServerSearch(server, normalizedSearch)
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
                  containerClassName="flex-1 sm:max-w-sm"
                  placeholder={t('searchPlaceholder')}
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </ResourceToolbar>
            </ResourcePanel.Toolbar>

            <ResourcePanel.Body className="bg-background/30">
              {isLoading ? (
                <ResourceGrid minColumnWidth={300}>
                  {Array.from({ length: 6 }).map((_, index) => (
                    <Skeleton key={index} className="moldy-skeleton-card h-48" />
                  ))}
                </ResourceGrid>
              ) : isFilteredEmpty ? (
                <EmptyState title={t('empty.filtered')} className="bg-card/50" />
              ) : (
                <ResourceGrid minColumnWidth={300}>
                  {filteredServers.map((server) => {
                    const healthEntry = healthByServer.get(server.id)
                    return (
                      <McpServerCard
                        key={server.id}
                        server={server}
                        healthEntry={healthEntry}
                        toolCountLabel={t('toolCount', {
                          count: server.last_tool_count ?? 0,
                        })}
                        endpointLabel={formatMcpEndpoint(server)}
                        lastResponseLabel={formatMcpDate(server.last_pinged_at)}
                        checkedAtLabel={
                          healthEntry ? formatMcpRelativeTime(healthEntry.checked_at, t) : null
                        }
                        checkNowLabel={t('actions.checkNow')}
                        checkNowAriaLabel={t('actions.checkNowFor', { name: server.name })}
                        manageLabel={t('actions.manage')}
                        publishLabel={t('actions.publish')}
                        onOpen={openDetail}
                        onPublish={setPublishServer}
                        onCheckNow={handleCheckNow}
                        checking={runHealthCheck.isPending}
                      />
                    )
                  })}
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
        onOpenChange={(open) => !open && closeDetail()}
      />
      <PublishWizard
        resource={
          publishServer
            ? {
                id: publishServer.id,
                resourceType: 'mcp',
                name: publishServer.name,
                description: publishServer.description,
                iconId: 'mcp',
                detailLabel: publishServer.name,
                detailSubhead: publishServer.transport,
              }
            : null
        }
        open={!!publishServer}
        onOpenChange={(open) => !open && setPublishServer(null)}
      />
    </ResourcePage>
  )
}
