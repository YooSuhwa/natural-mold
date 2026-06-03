'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import {
  PackageIcon,
  PlusIcon,
  SearchIcon,
  ServerIcon,
  SparklesIcon,
  Trash2Icon,
  WrenchIcon,
} from 'lucide-react'
import { DialogShell } from '@/components/shared/dialog-shell'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { useCredentialTypes } from '@/lib/hooks/use-credentials'
import { useAllMcpTools } from '@/lib/hooks/use-mcp-servers'
import type { Skill } from '@/lib/types/skill'
import type { ToolInstance } from '@/lib/types/tool'
import type { McpToolWithServer } from '@/lib/types/mcp'
import type { CredentialDefinition } from '@/lib/types/credential'

/**
 * 통합 Tools & Skills 다이얼로그.
 *
 * 좌측: 현재 선택 (도구 + MCP + Skill 통합, 타입 아이콘으로 구분)
 * 우측: [Catalog | My Tools | MCP | Skills] 탭 + 추가 가능 목록
 *
 * 서브에이전트 다이얼로그(2-column Current/Available)와 동일한 멘탈 모델이지만,
 * 우측만 다중 소스(카탈로그/도구/MCP/스킬)이라 탭으로 분리.
 */

type SelectedKind = 'tool' | 'mcp' | 'skill'
type AvailableKind = SelectedKind | 'catalog'
type DialogTab = 'catalog' | 'tools' | 'mcp' | 'skills'
export type ToolsSkillsDialogMode = 'all' | 'tools' | 'skills'

const MODE_META: Record<
  ToolsSkillsDialogMode,
  { titleKey: string; descriptionKey: string; IconComponent: typeof WrenchIcon }
> = {
  all: {
    titleKey: 'mode.all.title',
    descriptionKey: 'mode.all.description',
    IconComponent: WrenchIcon,
  },
  tools: {
    titleKey: 'mode.tools.title',
    descriptionKey: 'mode.tools.description',
    IconComponent: WrenchIcon,
  },
  skills: {
    titleKey: 'mode.skills.title',
    descriptionKey: 'mode.skills.description',
    IconComponent: SparklesIcon,
  },
}

interface ToolsSkillsDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void

  // Tools (user instances)
  allTools: ToolInstance[]
  selectedToolIds: Set<string>
  onToggleTool: (toolId: string) => void

  // MCP tools
  selectedMcpToolIds: Set<string>
  onToggleMcpTool: (toolId: string) => void

  // Skills
  allSkills: Skill[]
  selectedSkillIds: Set<string>
  onToggleSkill: (skillId: string) => void

  defaultTab?: DialogTab
  /**
   * 'all'   — 4탭 (Catalog/My Tools/MCP/Skills). form-mode + manual 페이지가 사용.
   * 'tools' — 3탭 (Catalog/My Tools/MCP). visual-settings Toolbox 노드 전용. Skills 제외.
   * 'skills' — 탭 없이 SubAgents 패턴 (Current/Available 단일 리스트). visual-settings Skills 노드 전용.
   */
  mode?: ToolsSkillsDialogMode
}

export function ToolsSkillsDialog({
  open,
  onOpenChange,
  allTools,
  selectedToolIds,
  onToggleTool,
  selectedMcpToolIds,
  onToggleMcpTool,
  allSkills,
  selectedSkillIds,
  onToggleSkill,
  defaultTab = 'catalog',
  mode = 'all',
}: ToolsSkillsDialogProps) {
  const initialTab: DialogTab = mode === 'tools' && defaultTab === 'skills' ? 'tools' : defaultTab
  const [tab, setTab] = useState<DialogTab>(initialTab)
  const { data: allMcpTools } = useAllMcpTools()

  const selectedTools = useMemo(
    () => allTools.filter((t) => selectedToolIds.has(t.id)),
    [allTools, selectedToolIds],
  )
  const selectedMcpTools = useMemo(
    () => (allMcpTools ?? []).filter((mt) => selectedMcpToolIds.has(mt.id)),
    [allMcpTools, selectedMcpToolIds],
  )
  const selectedSkills = useMemo(
    () => allSkills.filter((s) => selectedSkillIds.has(s.id)),
    [allSkills, selectedSkillIds],
  )

  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const { titleKey, descriptionKey, IconComponent } = MODE_META[mode]

  const showSkills = mode !== 'tools'
  const showToolsAndMcp = mode !== 'skills'
  const totalSelected =
    (showToolsAndMcp ? selectedTools.length + selectedMcpTools.length : 0) +
    (showSkills ? selectedSkills.length : 0)

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="console" height="tall">
      <DialogShell.Header
        icon={<IconComponent className="size-5" />}
        title={t(titleKey)}
        description={t(descriptionKey)}
      />
      <DialogShell.Body className="flex flex-col">
        <div className="grid min-h-0 flex-1 gap-6 md:grid-cols-2">
          <CurrentColumn
            total={totalSelected}
            tools={showToolsAndMcp ? selectedTools : []}
            mcpTools={showToolsAndMcp ? selectedMcpTools : []}
            skills={showSkills ? selectedSkills : []}
            onRemoveTool={onToggleTool}
            onRemoveMcp={onToggleMcpTool}
            onRemoveSkill={onToggleSkill}
          />

          <section className="flex min-h-0 flex-col">
            {mode === 'skills' ? (
              <SkillsPanel
                allSkills={allSkills}
                selectedSkillIds={selectedSkillIds}
                onToggle={onToggleSkill}
              />
            ) : (
              <Tabs
                value={tab}
                onValueChange={(v) => setTab(v as DialogTab)}
                className="flex min-h-0 w-full flex-1 flex-col"
              >
                <LineTabsList className="w-full justify-start">
                  <LineTabsTrigger value="catalog">
                    <PackageIcon className="size-3.5" /> {t('tabs.catalog')}
                  </LineTabsTrigger>
                  <LineTabsTrigger value="tools">
                    <WrenchIcon className="size-3.5" /> {t('tabs.tools')}
                  </LineTabsTrigger>
                  <LineTabsTrigger value="mcp">
                    <ServerIcon className="size-3.5" /> {t('tabs.mcp')}
                  </LineTabsTrigger>
                  {mode === 'all' && (
                    <LineTabsTrigger value="skills">
                      <SparklesIcon className="size-3.5" /> {t('tabs.skills')}
                    </LineTabsTrigger>
                  )}
                </LineTabsList>

                <TabsContent value="catalog" className="min-w-0 flex-1 overflow-y-auto pt-3">
                  <CatalogPanel />
                </TabsContent>

                <TabsContent value="tools" className="min-w-0 flex-1 overflow-y-auto pt-3">
                  <ToolsPanel
                    allTools={allTools}
                    selectedToolIds={selectedToolIds}
                    onToggle={onToggleTool}
                  />
                </TabsContent>

                <TabsContent value="mcp" className="min-w-0 flex-1 overflow-y-auto pt-3">
                  <McpPanel
                    selectedIds={selectedMcpToolIds}
                    onToggle={onToggleMcpTool}
                  />
                </TabsContent>

                {mode === 'all' && (
                  <TabsContent value="skills" className="min-w-0 flex-1 overflow-y-auto pt-3">
                    <SkillsPanel
                      allSkills={allSkills}
                      selectedSkillIds={selectedSkillIds}
                      onToggle={onToggleSkill}
                    />
                  </TabsContent>
                )}
              </Tabs>
            )}
          </section>
        </div>
      </DialogShell.Body>
    </DialogShell>
  )
}

// -- Left column: combined current selection ---------------------------------

function CurrentColumn({
  total,
  tools,
  mcpTools,
  skills,
  onRemoveTool,
  onRemoveMcp,
  onRemoveSkill,
}: {
  total: number
  tools: ToolInstance[]
  mcpTools: McpToolWithServer[]
  skills: Skill[]
  onRemoveTool: (id: string) => void
  onRemoveMcp: (id: string) => void
  onRemoveSkill: (id: string) => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  return (
    <section className="flex min-h-0 flex-col">
      <h3 className="mb-3 text-sm font-medium">{t('current', { count: total })}</h3>
      <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1 sm:h-[60vh]">
        {total === 0 ? (
          <EmptyBox>{t('selectedEmpty')}</EmptyBox>
        ) : (
          <>
            {tools.map((t) => (
              <SelectedRow
                key={`tool-${t.id}`}
                kind="tool"
                name={t.name}
                subtitle={t.definition_key}
                onRemove={() => onRemoveTool(t.id)}
              />
            ))}
            {mcpTools.map((mt) => (
              <SelectedRow
                key={`mcp-${mt.id}`}
                kind="mcp"
                name={mt.name}
                subtitle={`${mt.server_name} · MCP`}
                onRemove={() => onRemoveMcp(mt.id)}
              />
            ))}
            {skills.map((s) => (
              <SelectedRow
                key={`skill-${s.id}`}
                kind="skill"
                name={s.name}
                subtitle="Skill"
                onRemove={() => onRemoveSkill(s.id)}
              />
            ))}
          </>
        )}
      </div>
    </section>
  )
}

function SelectedRow({
  kind,
  name,
  subtitle,
  onRemove,
}: {
  kind: SelectedKind
  name: string
  subtitle: string
  onRemove: () => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  return (
    <div className="flex items-center gap-3 rounded-lg border p-3">
      <KindIcon kind={kind} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="truncate moldy-ui-caption text-muted-foreground">{subtitle}</p>
      </div>
      <Button
        size="sm"
        variant="ghost"
        onClick={onRemove}
        className="shrink-0"
        aria-label={t('removeNamed', { name })}
      >
        <Trash2Icon className="size-3.5" />
        {t('remove')}
      </Button>
    </div>
  )
}

function KindIcon({ kind }: { kind: AvailableKind }) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog.kind')
  const config = {
    tool: {
      Icon: WrenchIcon,
      className: 'moldy-dashboard-action-icon moldy-status-accent',
      label: t('tool'),
    },
    mcp: {
      Icon: ServerIcon,
      className: 'moldy-dashboard-action-icon moldy-status-info',
      label: t('mcp'),
    },
    skill: {
      Icon: SparklesIcon,
      className: 'moldy-dashboard-action-icon moldy-status-success',
      label: t('skill'),
    },
    catalog: {
      Icon: PackageIcon,
      className: 'moldy-dashboard-action-icon moldy-status-warn',
      label: t('catalog'),
    },
  }[kind]
  const { Icon, className, label } = config
  return (
    <span
      className={`flex size-8 shrink-0 items-center justify-center rounded-md ${className}`}
      aria-label={label}
      title={label}
    >
      <Icon className="size-4" />
    </span>
  )
}

// -- Right column tabs -------------------------------------------------------

function CatalogPanel() {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const { data: definitions } = useCredentialTypes()
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const all = (definitions ?? []).filter((d) => d.category !== 'llm')
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter(
      (d) =>
        d.display_name.toLowerCase().includes(q) ||
        d.key.toLowerCase().includes(q) ||
        (d.category ?? '').toLowerCase().includes(q),
    )
  }, [definitions, query])

  if (!definitions) {
    return (
      <div className="space-y-3">
        <SearchBar value={query} onChange={setQuery} />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  return (
    <AvailableList
      query={query}
      onQueryChange={setQuery}
      items={filtered.map((d) => (
        <CatalogRow key={d.key} definition={d} />
      ))}
      emptyText={t('catalogEmpty')}
    />
  )
}

function CatalogRow({ definition }: { definition: CredentialDefinition }) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <KindIcon kind="catalog" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium">{definition.display_name}</p>
          <Badge variant="secondary" className="shrink-0 moldy-ui-micro">
            {definition.category}
          </Badge>
        </div>
        <p className="truncate font-mono moldy-ui-caption text-muted-foreground">
          {definition.key}
        </p>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="shrink-0"
        render={
          <Link
            href={`/tools?create=${encodeURIComponent(definition.key)}`}
            aria-label={t('createNamed', { name: definition.display_name })}
          />
        }
      >
        <PlusIcon className="size-3.5" />
        {t('create')}
      </Button>
    </div>
  )
}

function ToolsPanel({
  allTools,
  selectedToolIds,
  onToggle,
}: {
  allTools: ToolInstance[]
  selectedToolIds: Set<string>
  onToggle: (id: string) => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const [query, setQuery] = useState('')
  const available = useMemo(() => {
    const q = query.trim().toLowerCase()
    return allTools
      .filter((t) => !selectedToolIds.has(t.id))
      .filter((t) =>
        !q
          ? true
          : t.name.toLowerCase().includes(q) ||
            (t.description ?? '').toLowerCase().includes(q) ||
            t.definition_key.toLowerCase().includes(q),
      )
  }, [allTools, selectedToolIds, query])

  return (
    <AvailableList
      query={query}
      onQueryChange={setQuery}
      items={available.map((t) => (
        <AvailableRow
          key={t.id}
          kind="tool"
          name={t.name}
          subtitle={t.definition_key}
          description={t.description}
          onAdd={() => onToggle(t.id)}
        />
      ))}
      emptyText={
        allTools.length === 0
          ? t('toolsEmpty')
          : t('noResults')
      }
    />
  )
}

function McpPanel({
  selectedIds,
  onToggle,
}: {
  selectedIds: Set<string>
  onToggle: (id: string) => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const { data: tools, isLoading } = useAllMcpTools()
  const [query, setQuery] = useState('')

  const list = useMemo<McpToolWithServer[]>(() => tools ?? [], [tools])
  const available = useMemo(() => {
    const q = query.trim().toLowerCase()
    return list
      .filter((t) => !selectedIds.has(t.id))
      .filter((t) =>
        !q
          ? true
          : t.name.toLowerCase().includes(q) ||
            (t.description ?? '').toLowerCase().includes(q) ||
            t.server_name.toLowerCase().includes(q),
      )
  }, [list, selectedIds, query])

  if (isLoading) {
    return <Skeleton className="h-40 w-full" />
  }

  return (
    <AvailableList
      query={query}
      onQueryChange={setQuery}
      items={available.map((t) => (
        <AvailableRow
          key={t.id}
          kind="mcp"
          name={t.name}
          subtitle={`${t.server_name} · MCP`}
          description={t.description}
          onAdd={() => onToggle(t.id)}
        />
      ))}
      emptyText={
        list.length === 0
          ? t('mcpEmpty')
          : t('noResults')
      }
    />
  )
}

function SkillsPanel({
  allSkills,
  selectedSkillIds,
  onToggle,
}: {
  allSkills: Skill[]
  selectedSkillIds: Set<string>
  onToggle: (id: string) => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const [query, setQuery] = useState('')
  const available = useMemo(() => {
    const q = query.trim().toLowerCase()
    return allSkills
      .filter((s) => !selectedSkillIds.has(s.id))
      .filter((s) =>
        !q
          ? true
          : s.name.toLowerCase().includes(q) ||
            (s.description ?? '').toLowerCase().includes(q),
      )
  }, [allSkills, selectedSkillIds, query])

  return (
    <AvailableList
      query={query}
      onQueryChange={setQuery}
      items={available.map((s) => (
        <AvailableRow
          key={s.id}
          kind="skill"
          name={s.name}
          subtitle="Skill"
          description={s.description}
          onAdd={() => onToggle(s.id)}
        />
      ))}
      emptyText={
        allSkills.length === 0
          ? t('skillsEmpty')
          : t('noResults')
      }
    />
  )
}

// -- Shared shells ----------------------------------------------------------

function AvailableList({
  query,
  onQueryChange,
  items,
  emptyText,
}: {
  query: string
  onQueryChange: (v: string) => void
  items: React.ReactNode[]
  emptyText: string
}) {
  return (
    <div className="space-y-3">
      <SearchBar value={query} onChange={onQueryChange} />
      <div className="space-y-2">
        {items.length === 0 ? <EmptyBox>{emptyText}</EmptyBox> : items}
      </div>
    </div>
  )
}

function SearchBar({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  return (
    <div className="relative">
      <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t('search')}
        className="pl-9 focus-visible:border-input focus-visible:ring-0"
      />
    </div>
  )
}

function AvailableRow({
  kind,
  name,
  subtitle,
  description,
  onAdd,
}: {
  kind: AvailableKind
  name: string
  subtitle: string
  description?: string | null
  onAdd: () => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <KindIcon kind={kind} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="truncate moldy-ui-caption text-muted-foreground">{subtitle}</p>
        {description && (
          <p className="mt-0.5 line-clamp-2 moldy-ui-caption text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={onAdd}
        className="shrink-0"
        aria-label={t('addNamed', { name })}
      >
        <PlusIcon className="size-3.5" />
        {t('add')}
      </Button>
    </div>
  )
}

function EmptyBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-32 items-center justify-center rounded-lg border border-dashed text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}
