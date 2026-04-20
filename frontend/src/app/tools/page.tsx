'use client'

import { useState, useMemo } from 'react'
import { toast } from 'sonner'
import {
  PlusIcon,
  WrenchIcon,
  LinkIcon,
  GlobeIcon,
  Trash2Icon,
  Loader2Icon,
  SparklesIcon,
  KeyIcon,
  CheckCircleIcon,
  PlugIcon,
  SearchIcon,
  ShieldCheckIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useTools, useDeleteTool, useMCPServers, useUpdateToolAuthConfig } from '@/lib/hooks/use-tools'
import { useConnections } from '@/lib/hooks/use-connections'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { SearchInput } from '@/components/shared/search-input'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import { AddToolDialog } from '@/components/tool/add-tool-dialog'
import { ConnectionBindingDialog } from '@/components/connection/connection-binding-dialog'
import { MCPServerGroupCard } from '@/components/tool/mcp-server-group-card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import type { Connection, MCPServerListItem, Tool } from '@/lib/types'
import { isPrebuiltProviderName } from '@/lib/types'

type ToolFilter = 'all' | 'builtin' | 'prebuilt' | 'mcp' | 'custom'

const ALL_TAGS = [
  'search',
  'web',
  'email',
  'calendar',
  'communication',
  'google',
  'naver',
  'korean',
  'news',
  'image',
  'shopping',
  'local',
  'productivity',
  'scraping',
  'utility',
  'free',
] as const

const TOOL_TYPE_STYLES: Record<
  string,
  { icon: typeof SparklesIcon; color: string; badgeClass: string }
> = {
  builtin: {
    icon: SparklesIcon,
    color: 'bg-violet-500/10 text-violet-600',
    badgeClass: 'bg-violet-100 text-violet-700 hover:bg-violet-100',
  },
  prebuilt: {
    icon: PlugIcon,
    color: 'bg-sky-500/10 text-sky-600',
    badgeClass: 'bg-sky-100 text-sky-700 hover:bg-sky-100',
  },
  mcp: {
    icon: LinkIcon,
    color: 'bg-primary/10 text-primary',
    badgeClass: '',
  },
  custom: {
    icon: GlobeIcon,
    color: 'bg-muted text-muted-foreground',
    badgeClass: '',
  },
}

type AuthStatus = 'not_configured' | 'configured'

function getAuthStatus(
  tool: Tool,
  prebuiltConfiguredProviders: Set<string>,
): AuthStatus {
  // PREBUILT는 M3부터 per-user connection이 SOT. provider_name에 해당하는
  // default connection이 credential_id를 가진 active 상태면 configured.
  // legacy(credential_id/auth_config) fallback은 provider_name이 없는 row에
  // 한해 유지 (M6까지 이행 tolerance).
  if (tool.type === 'prebuilt' && tool.provider_name) {
    return prebuiltConfiguredProviders.has(tool.provider_name)
      ? 'configured'
      : 'not_configured'
  }
  if (tool.credential_id) return 'configured'
  // Note: server masks string values to "***" before sending. The mask itself
  // is non-empty, so a configured legacy auth_config still resolves to 'configured'
  // here — that's intentional (mask presence == real value exists on server).
  const hasAuth =
    tool.auth_config &&
    Object.values(tool.auth_config).some((v) => typeof v === 'string' && v.length > 0)
  if (hasAuth) return 'configured'
  return 'not_configured'
}

const AUTH_STATUS_STYLES: Record<
  AuthStatus,
  { color: string; badgeClass: string; icon: typeof KeyIcon }
> = {
  not_configured: {
    color: 'bg-amber-500/10 text-amber-600',
    badgeClass: 'bg-amber-100 text-amber-700 hover:bg-amber-100',
    icon: KeyIcon,
  },
  configured: {
    color: 'bg-emerald-500/10 text-emerald-600',
    badgeClass: 'bg-emerald-100 text-emerald-700 hover:bg-emerald-100',
    icon: CheckCircleIcon,
  },
}

function ToolCardSkeleton() {
  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-start justify-between">
          <Skeleton className="size-9 rounded-lg" />
          <Skeleton className="h-5 w-14" />
        </div>
        <Skeleton className="mt-2 h-5 w-32" />
        <Skeleton className="h-4 w-full" />
      </CardHeader>
    </Card>
  )
}

function ToolCard({
  tool,
  onDelete,
  isDeleting,
  onShowDetail,
  prebuiltConfiguredProviders,
}: {
  tool: Tool
  onDelete: (tool: Tool) => void
  isDeleting: boolean
  onShowDetail: (tool: Tool) => void
  prebuiltConfiguredProviders: Set<string>
}) {
  const t = useTranslations('tool.page')
  const tCustomAuth = useTranslations('tool.customAuth')
  const meta = TOOL_TYPE_STYLES[tool.type] ?? TOOL_TYPE_STYLES.custom
  const Icon = meta.icon
  const isPrebuilt = tool.type === 'prebuilt'
  const isCustom = tool.type === 'custom'
  const showAuth = isPrebuilt || isCustom
  const authStatus = showAuth ? getAuthStatus(tool, prebuiltConfiguredProviders) : null
  const [authDialogOpen, setAuthDialogOpen] = useState(false)
  // Prebuilt cards swap their type badge for an auth-status badge (system tools
  // are always present, so auth-state IS the salient signal). Custom cards keep
  // their "Custom" type badge and surface auth-state only via the footer button.
  const pStyle = isPrebuilt && authStatus ? AUTH_STATUS_STYLES[authStatus] : null
  const isDeletable = !tool.is_system

  const badgeTexts: Record<string, string> = {
    builtin: t('badge.builtin'),
    prebuilt: t('badge.prebuilt'),
    mcp: t('badge.mcp'),
    custom: t('badge.custom'),
  }

  const prebuiltTexts: Record<AuthStatus, { badge: string; buttonLabel: string }> = {
    not_configured: { badge: t('prebuilt.notConfigured'), buttonLabel: t('prebuilt.setKey') },
    configured: { badge: t('prebuilt.configured'), buttonLabel: t('prebuilt.changeKey') },
  }

  const customAuthLabels: Record<AuthStatus, string> = {
    not_configured: tCustomAuth('setKey'),
    configured: tCustomAuth('changeKey'),
  }

  const pText = isPrebuilt && authStatus ? prebuiltTexts[authStatus] : null

  const updateAuth = useUpdateToolAuthConfig()

  // ConnectionBindingDialog는 Connection만 변경(rotate/create/clear)하므로, custom tool의
  // runtime credential을 실제로 갈아끼우려면 tool.credential_id도 동기화해야 한다.
  // - legacy custom tool (connection_id=null) 첫 바인딩 → connection.credential_id 반영
  // - bridge override 상태에서 user가 명시적으로 connection을 바꾼 경우 → bridge 해소
  // - user가 None을 골라 auth를 명시 해제한 경우 → tool.credential_id도 null로 클리어
  // dialog의 "saved" toast 후에 tool sync가 silent partial failure 되지 않도록 await + onError.
  // M6에서 tool.connection_id 직접 binding으로 일괄 정리. 백엔드 변경 0건 (기존 endpoint 활용).
  const handleCustomBound = async (connection: Connection) => {
    if (tool.type !== 'custom' || connection.credential_id === tool.credential_id) return
    try {
      await updateAuth.mutateAsync({
        id: tool.id,
        authConfig: (tool.auth_config as Record<string, unknown> | null) ?? {},
        credentialId: connection.credential_id,
      })
    } catch {
      toast.error(tCustomAuth('toolSyncFailed'))
    }
  }

  return (
    <Card
      className="group h-full flex flex-col transition-colors hover:border-primary/40 cursor-pointer"
      onClick={() => onShowDetail(tool)}
    >
      <CardHeader className="flex-1">
        <div className="flex items-start justify-between">
          <div
            className={`flex size-9 items-center justify-center rounded-lg ${
              pStyle ? pStyle.color : meta.color
            }`}
          >
            {pStyle ? <pStyle.icon className="size-4" /> : <Icon className="size-4" />}
          </div>
          <div className="flex items-center gap-1.5">
            {tool.agent_count > 0 && (
              <span className="text-[10px] text-muted-foreground">
                {t('agentCount', { count: tool.agent_count })}
              </span>
            )}
            <Badge
              className={pStyle ? pStyle.badgeClass : meta.badgeClass}
              variant={pStyle || meta.badgeClass ? undefined : 'secondary'}
            >
              {pText ? pText.badge : (badgeTexts[tool.type] ?? badgeTexts.custom)}
            </Badge>
          </div>
        </div>
        <CardTitle className="mt-2 text-sm">{tool.name}</CardTitle>
        {tool.description && (
          <CardDescription className="line-clamp-2 text-xs">{tool.description}</CardDescription>
        )}
        {tool.tags && tool.tags.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {tool.tags.map((tag) => (
              <span
                key={tag}
                className="inline-block rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </CardHeader>

      <CardFooter className="gap-2" onClick={(e) => e.stopPropagation()}>
        {isPrebuilt && pText ? (
          <>
            <Button
              variant="outline"
              size="sm"
              className="w-full cursor-pointer"
              onClick={() => setAuthDialogOpen(true)}
            >
              <KeyIcon className="size-3.5" data-icon="inline-start" />
              {pText.buttonLabel}
            </Button>
            {tool.provider_name && isPrebuiltProviderName(tool.provider_name) ? (
              <ConnectionBindingDialog
                type="prebuilt"
                providerName={tool.provider_name}
                toolName={tool.name}
                triggerContext="tool-edit"
                open={authDialogOpen}
                onOpenChange={setAuthDialogOpen}
              />
            ) : (
              // provider_name이 NULL이거나 알 수 없는 prebuilt row(m10 매핑 실패 등)는
              // legacy `tool.credential_id` 기반으로 backend가 실행하므로, UI도 custom
              // 플로우로 위임해 rotate/clear 경로를 유지한다 (M6 cleanup까지 tolerance).
              <ConnectionBindingDialog
                type="custom"
                tool={tool}
                toolName={tool.name}
                triggerContext="tool-edit"
                open={authDialogOpen}
                onOpenChange={setAuthDialogOpen}
                onBound={handleCustomBound}
              />
            )}
          </>
        ) : isCustom && authStatus ? (
          <>
            <Button
              variant="outline"
              size="sm"
              className="flex-1 cursor-pointer"
              onClick={() => setAuthDialogOpen(true)}
            >
              <KeyIcon className="size-3.5" data-icon="inline-start" />
              {customAuthLabels[authStatus]}
            </Button>
            <ConnectionBindingDialog
              type="custom"
              tool={tool}
              toolName={tool.name}
              triggerContext="tool-edit"
              open={authDialogOpen}
              onOpenChange={setAuthDialogOpen}
              onBound={handleCustomBound}
            />
            {isDeletable && (
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground hover:text-destructive"
                onClick={() => onDelete(tool)}
                disabled={isDeleting}
                aria-label={t('deleteButton')}
              >
                {isDeleting ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <Trash2Icon className="size-3.5" />
                )}
              </Button>
            )}
          </>
        ) : isDeletable ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full text-muted-foreground hover:text-destructive"
            onClick={() => onDelete(tool)}
            disabled={isDeleting}
          >
            {isDeleting ? (
              <Loader2Icon className="size-3.5 animate-spin" data-icon="inline-start" />
            ) : (
              <Trash2Icon className="size-3.5" data-icon="inline-start" />
            )}
            {t('deleteButton')}
          </Button>
        ) : (
          <div className="flex w-full items-center justify-center gap-1.5 text-xs text-muted-foreground">
            <ShieldCheckIcon className="size-3.5" />
            <span>{t('systemTool')}</span>
          </div>
        )}
      </CardFooter>
    </Card>
  )
}

function ToolSection({
  label,
  count,
  tools,
  onDelete,
  isDeleting,
  onShowDetail,
  prebuiltConfiguredProviders,
}: {
  label: string
  count: number
  tools: Tool[]
  onDelete: (tool: Tool) => void
  isDeleting: boolean
  onShowDetail: (tool: Tool) => void
  prebuiltConfiguredProviders: Set<string>
}) {
  return (
    <section>
      <h2 className="text-sm font-semibold mb-3 text-foreground/80">
        {label} <span className="text-muted-foreground font-normal">({count})</span>
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {tools.map((tool) => (
          <ToolCard
            key={tool.id}
            tool={tool}
            onDelete={onDelete}
            isDeleting={isDeleting}
            onShowDetail={onShowDetail}
            prebuiltConfiguredProviders={prebuiltConfiguredProviders}
          />
        ))}
      </div>
    </section>
  )
}

export default function ToolsPage() {
  const { data: tools, isLoading } = useTools()
  const { data: mcpServers, isLoading: mcpLoading } = useMCPServers()
  // PREBUILT connection 목록 — configured 상태 판정에 사용. type 필터만 적용해
  // tool.provider_name 매핑용 Set 구성.
  const { data: prebuiltConnections } = useConnections({ type: 'prebuilt' })
  const deleteTool = useDeleteTool()
  const t = useTranslations('tool.page')
  const tc = useTranslations('common')
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<ToolFilter>('all')
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set())
  const [detailTool, setDetailTool] = useState<Tool | null>(null)
  const [deletingToolTarget, setDeletingToolTarget] = useState<Tool | null>(null)

  // provider_name 중 default + credential_id가 붙은 것만 "configured"로 간주.
  // Set으로 만들어 ToolCard에서 O(1) lookup.
  const prebuiltConfiguredProviders = useMemo(() => {
    const set = new Set<string>()
    for (const conn of prebuiltConnections ?? []) {
      if (conn.is_default && conn.credential_id && conn.status === 'active') {
        set.add(conn.provider_name)
      }
    }
    return set
  }, [prebuiltConnections])

  const filterOptions: { value: ToolFilter; label: string }[] = [
    { value: 'all', label: t('filter.all') },
    { value: 'builtin', label: t('filter.builtin') },
    { value: 'prebuilt', label: t('filter.prebuilt') },
    { value: 'custom', label: t('filter.custom') },
    { value: 'mcp', label: t('filter.mcp') },
  ]

  const availableTags = useMemo(() => {
    if (!tools) return []
    const tagSet = new Set<string>()
    for (const tl of tools) {
      if (tl.tags) tl.tags.forEach((tag) => tagSet.add(tag))
    }
    return ALL_TAGS.filter((tag) => tagSet.has(tag))
  }, [tools])

  function toggleTag(tag: string) {
    setSelectedTags((prev) => {
      const next = new Set(prev)
      if (next.has(tag)) next.delete(tag)
      else next.add(tag)
      return next
    })
  }

  function matchesQuery(value: string | null | undefined, q: string) {
    return value?.toLowerCase().includes(q) ?? false
  }

  function matchesTags(toolTags: string[] | null | undefined, selected: Set<string>) {
    if (selected.size === 0) return true
    if (!toolTags) return false
    return Array.from(selected).some((tag) => toolTags.includes(tag))
  }

  const nonMCPTools = useMemo(() => {
    if (!tools) return [] as Tool[]
    const q = search.toLowerCase()
    return tools.filter((tl) => {
      if (tl.type === 'mcp') return false
      if (search && !matchesQuery(tl.name, q) && !matchesQuery(tl.description, q)) return false
      if (!matchesTags(tl.tags, selectedTags)) return false
      return true
    })
  }, [tools, search, selectedTags])

  const mcpToolsByServer = useMemo(() => {
    const map = new Map<string, Tool[]>()
    if (!tools) return map
    for (const tl of tools) {
      if (tl.type !== 'mcp' || !tl.mcp_server_id) continue
      const list = map.get(tl.mcp_server_id) ?? []
      list.push(tl)
      map.set(tl.mcp_server_id, list)
    }
    return map
  }, [tools])

  const filteredMCPServers = useMemo(() => {
    if (!mcpServers) return [] as Array<{ server: MCPServerListItem; matchedByTool: boolean }>
    const q = search.toLowerCase()
    return mcpServers
      .map((server) => {
        const serverTools = mcpToolsByServer.get(server.id) ?? []
        const serverMatch = !search || matchesQuery(server.name, q)
        const toolMatch = search && serverTools.some((tl) => matchesQuery(tl.name, q))
        if (search && !serverMatch && !toolMatch) return null
        if (selectedTags.size > 0) {
          const anyTagMatch = serverTools.some((tl) => matchesTags(tl.tags, selectedTags))
          if (!anyTagMatch) return null
        }
        return { server, matchedByTool: !!toolMatch }
      })
      .filter((entry): entry is { server: MCPServerListItem; matchedByTool: boolean } =>
        entry !== null,
      )
  }, [mcpServers, mcpToolsByServer, search, selectedTags])

  const sectionTools = useMemo(() => {
    return {
      builtin: nonMCPTools.filter((tl) => tl.type === 'builtin'),
      prebuilt: nonMCPTools.filter((tl) => tl.type === 'prebuilt'),
      custom: nonMCPTools.filter((tl) => tl.type === 'custom'),
    }
  }, [nonMCPTools])

  const counts = useMemo(() => {
    if (!tools) return { all: 0, builtin: 0, prebuilt: 0, mcp: 0, custom: 0 }
    return {
      all: tools.length,
      builtin: tools.filter((tl) => tl.type === 'builtin').length,
      prebuilt: tools.filter((tl) => tl.type === 'prebuilt').length,
      mcp: mcpServers?.length ?? 0,
      custom: tools.filter((tl) => tl.type === 'custom' && !tl.is_system).length,
    }
  }, [tools, mcpServers])

  const showSection = (key: ToolFilter) => filter === 'all' || filter === key
  const totalLoading = isLoading || mcpLoading
  const totalVisibleCount =
    (showSection('builtin') ? sectionTools.builtin.length : 0) +
    (showSection('prebuilt') ? sectionTools.prebuilt.length : 0) +
    (showSection('mcp') ? filteredMCPServers.length : 0) +
    (showSection('custom') ? sectionTools.custom.length : 0)

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title={t('title')}
        action={
          <AddToolDialog
            trigger={
              <Button>
                <PlusIcon className="size-4" data-icon="inline-start" />
                {t('addTool')}
              </Button>
            }
          />
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-1.5 flex-wrap">
          {filterOptions.map((opt) => (
            <Button
              key={opt.value}
              variant={filter === opt.value ? 'default' : 'outline'}
              size="sm"
              onClick={() => setFilter(opt.value)}
              className="cursor-pointer"
            >
              {opt.label}
              {counts[opt.value] > 0 && (
                <span
                  className={`ml-1 text-xs ${filter === opt.value ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}
                >
                  {counts[opt.value]}
                </span>
              )}
            </Button>
          ))}
        </div>
        <SearchInput
          containerClassName="w-full sm:w-64"
          placeholder={t('searchPlaceholder')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Tag Filter */}
      {availableTags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground mr-1">{t('tagLabel')}</span>
          {availableTags.map((tag) => (
            <Badge
              key={tag}
              variant={selectedTags.has(tag) ? 'default' : 'outline'}
              className="cursor-pointer text-xs"
              onClick={() => toggleTag(tag)}
            >
              {tag}
            </Badge>
          ))}
          {selectedTags.size > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs text-muted-foreground"
              onClick={() => setSelectedTags(new Set())}
            >
              {t('resetFilter')}
            </Button>
          )}
        </div>
      )}

      {totalLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <ToolCardSkeleton key={i} />
          ))}
        </div>
      ) : totalVisibleCount > 0 ? (
        <div className="flex flex-col gap-8">
          {showSection('builtin') && sectionTools.builtin.length > 0 && (
            <ToolSection
              label={t('section.builtin')}
              count={sectionTools.builtin.length}
              tools={sectionTools.builtin}
              onDelete={setDeletingToolTarget}
              isDeleting={deleteTool.isPending}
              onShowDetail={setDetailTool}
              prebuiltConfiguredProviders={prebuiltConfiguredProviders}
            />
          )}
          {showSection('prebuilt') && sectionTools.prebuilt.length > 0 && (
            <ToolSection
              label={t('section.prebuilt')}
              count={sectionTools.prebuilt.length}
              tools={sectionTools.prebuilt}
              onDelete={setDeletingToolTarget}
              isDeleting={deleteTool.isPending}
              onShowDetail={setDetailTool}
              prebuiltConfiguredProviders={prebuiltConfiguredProviders}
            />
          )}
          {showSection('mcp') && filteredMCPServers.length > 0 && (
            <section>
              <h2 className="text-sm font-semibold mb-3 text-foreground/80">
                {t('section.mcp')}{' '}
                <span className="text-muted-foreground font-normal">
                  ({filteredMCPServers.length})
                </span>
              </h2>
              <div className="flex flex-col gap-3">
                {filteredMCPServers.map(({ server, matchedByTool }) => (
                  <MCPServerGroupCard
                    key={server.id}
                    server={server}
                    tools={mcpToolsByServer.get(server.id) ?? []}
                    defaultOpen={matchedByTool}
                  />
                ))}
              </div>
            </section>
          )}
          {showSection('custom') && sectionTools.custom.length > 0 && (
            <ToolSection
              label={t('section.custom')}
              count={sectionTools.custom.length}
              tools={sectionTools.custom}
              onDelete={setDeletingToolTarget}
              isDeleting={deleteTool.isPending}
              onShowDetail={setDetailTool}
              prebuiltConfiguredProviders={prebuiltConfiguredProviders}
            />
          )}
        </div>
      ) : search || filter !== 'all' ? (
        <EmptyState
          icon={<SearchIcon className="size-6" />}
          title={t('noSearchResults')}
          description={search ? t('noResultsForQuery', { query: search }) : t('noFilterResults')}
          action={
            <Button
              variant="outline"
              onClick={() => {
                setSearch('')
                setFilter('all')
              }}
            >
              {t('resetFilters')}
            </Button>
          }
        />
      ) : (
        <EmptyState
          icon={<WrenchIcon className="size-6" />}
          title={t('empty.title')}
          description={t('empty.description')}
          action={
            <AddToolDialog
              trigger={
                <Button>
                  <PlusIcon className="size-4" data-icon="inline-start" />
                  {t('addTool')}
                </Button>
              }
            />
          }
        />
      )}

      {/* Tool Detail Dialog */}
      <Dialog
        open={!!detailTool}
        onOpenChange={(open) => {
          if (!open) setDetailTool(null)
        }}
      >
        <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-auto">
          {detailTool && (
            <>
              <DialogHeader>
                <DialogTitle>{detailTool.name}</DialogTitle>
                <DialogDescription>{detailTool.description}</DialogDescription>
              </DialogHeader>

              <div className="mt-6 space-y-5">
                {/* Meta */}
                <div className="flex flex-wrap gap-2">
                  <Badge variant="secondary">{detailTool.type}</Badge>
                  {detailTool.tags?.map((tag) => (
                    <Badge key={tag} variant="outline" className="text-xs">
                      {tag}
                    </Badge>
                  ))}
                </div>

                {/* Stats */}
                <div className="flex gap-4 text-sm text-muted-foreground">
                  <span>{t('detail.agentUsage', { count: detailTool.agent_count })}</span>
                  {detailTool.is_system && <span>{t('detail.systemTool')}</span>}
                </div>

                {/* Auth Info */}
                {detailTool.auth_type && (
                  <div className="space-y-1">
                    <h4 className="text-sm font-medium">{t('detail.authMethod')}</h4>
                    <p className="text-sm text-muted-foreground">{detailTool.auth_type}</p>
                  </div>
                )}

                {/* Parameters Schema */}
                {detailTool.parameters_schema && (
                  <div className="space-y-2">
                    <h4 className="text-sm font-medium">{t('detail.parameters')}</h4>
                    <div className="rounded-lg border">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b bg-muted/50">
                            <th className="px-3 py-2 text-left font-medium">
                              {t('detail.paramName')}
                            </th>
                            <th className="px-3 py-2 text-left font-medium">
                              {t('detail.paramType')}
                            </th>
                            <th className="px-3 py-2 text-left font-medium">
                              {t('detail.paramDescription')}
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {detailTool.parameters_schema.properties
                            ? Object.entries(
                                detailTool.parameters_schema.properties as Record<
                                  string,
                                  Record<string, unknown>
                                >,
                              ).map(([key, val]) => {
                                const required =
                                  Array.isArray(detailTool.parameters_schema?.required) &&
                                  (detailTool.parameters_schema.required as string[]).includes(key)
                                return (
                                  <tr key={key} className="border-b last:border-0">
                                    <td className="px-3 py-2 font-mono">
                                      {key}
                                      {required && (
                                        <span className="ml-0.5 text-destructive">*</span>
                                      )}
                                    </td>
                                    <td className="px-3 py-2 text-muted-foreground">
                                      {String(val.type ?? '')}
                                    </td>
                                    <td className="px-3 py-2 text-muted-foreground">
                                      {String(val.description ?? '')}
                                    </td>
                                  </tr>
                                )
                              })
                            : null}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* API URL */}
                {detailTool.api_url && (
                  <div className="space-y-1">
                    <h4 className="text-sm font-medium">API URL</h4>
                    <p className="rounded bg-muted px-2 py-1 font-mono text-xs break-all">
                      {detailTool.http_method} {detailTool.api_url}
                    </p>
                  </div>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Tool Delete Confirm */}
      <DeleteConfirmDialog
        open={!!deletingToolTarget}
        onOpenChange={(v) => !v && setDeletingToolTarget(null)}
        title={t('deleteConfirm')}
        description={deletingToolTarget?.name}
        cancelLabel={tc('cancel')}
        confirmLabel={tc('delete')}
        isPending={deleteTool.isPending}
        onConfirm={() => {
          if (deletingToolTarget) {
            deleteTool.mutate(deletingToolTarget.id, {
              onSuccess: () => setDeletingToolTarget(null),
              onError: () => toast.error(t('toast.deleteFailed')),
            })
          }
        }}
      />
    </div>
  )
}
