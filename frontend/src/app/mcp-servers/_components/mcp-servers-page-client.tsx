'use client'

import { PermissionsLoadError } from '@/components/shared/permissions-load-error'
import { useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Download, Plus, Server, Upload } from 'lucide-react'
import { useTranslations } from 'next-intl'

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
import { useMcpServers } from '@/lib/hooks/use-mcp-servers'
import { useCapability } from '@/lib/hooks/use-resource-permissions'
import { useResourceAccess } from '@/lib/authz/use-resource-access'
import type { McpServer } from '@/lib/types/mcp'
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
import { useMcpExport } from './use-mcp-export'
import { useMcpHealthCheck } from './use-mcp-health-check'

export function McpServersPageClient() {
  const t = useTranslations('mcp.page')
  const router = useRouter()
  const searchParams = useSearchParams()
  const deepLinkDetailId = searchParams.get('detailId')
  const { data: servers, isLoading } = useMcpServers()
  const health = useMcpHealthCheck()
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
  const exporter = useMcpExport()
  // Plan 13.9: registering (wizard, import) needs cap_register_mcp; per-server test,
  // health check and edits need the server's can_edit, so runners only see the card.
  const canCreate = useCapability('cap_register_mcp')
  const access = useResourceAccess('mcp_server', servers)

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
          {canCreate ? (
            <Button variant="outline" onClick={() => setImportOpen(true)}>
              <Download className="size-4" />
              {t('actions.import')}
            </Button>
          ) : null}
          <Button variant="outline" onClick={exporter.handleExport} disabled={exporter.isPending}>
            <Upload className="size-4" />
            {t('actions.export')}
          </Button>
          {canCreate ? (
            <Button onClick={() => setWizardOpen(true)}>
              <Plus className="size-4" />
              {t('actions.new')}
            </Button>
          ) : null}
        </div>
      }
    >
      <PermissionsLoadError error={access.error} onRetry={() => void access.refetch()} />
      <ResourcePanel>
        {isInitialEmpty ? (
          <ResourcePanel.Body>
            <EmptyState
              icon={<Server className="size-6" />}
              title={t('empty.title')}
              description={t('empty.description')}
              className="bg-card/50"
              action={
                canCreate ? (
                  <Button onClick={() => setWizardOpen(true)}>
                    <Plus className="size-4" />
                    {t('empty.action')}
                  </Button>
                ) : undefined
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
                    const healthEntry = health.healthByServer.get(server.id)
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
                        onCheckNow={health.checkNow}
                        checking={health.checking}
                        canEdit={access.can(server, 'can_edit')}
                        canShare={access.can(server, 'can_share')}
                        myPermissions={access.permissionsOf(server.id)}
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
