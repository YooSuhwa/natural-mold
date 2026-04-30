'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import {
  PlusIcon,
  SearchIcon,
  ServerIcon,
  SparklesIcon,
  Trash2Icon,
  WrenchIcon,
  PackageIcon,
} from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
 * 사이드 nav 없이 4-tab 평탄 구조 (사용자 결정):
 *   [Catalog] [My Tools] [MCP] [Skills]
 *
 * 각 탭은 서브에이전트 다이얼로그와 동일한 2-column (Current | Available)
 * 패턴. agent 만들기 도중 도구/스킬을 다 한 곳에서 처리하는 게 목표라
 * 사용자가 다이얼로그 닫고 /tools, /skills로 다녀올 필요 없음.
 *
 * Catalog 탭은 현재 외부 링크(`/tools`)로 인스턴스 만들기를 안내. 다음
 * follow-up: inline credential picker → 즉시 인스턴스 생성 + binding.
 */
interface ToolsSkillsDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void

  // Tools (user instances) — `tools` 테이블 기반
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

  /** 시작 탭 — ToolboxNode면 'tools', SkillsNode면 'skills' 등. */
  defaultTab?: 'catalog' | 'tools' | 'mcp' | 'skills'
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
}: ToolsSkillsDialogProps) {
  const [tab, setTab] = useState<'catalog' | 'tools' | 'mcp' | 'skills'>(
    defaultTab,
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] flex-col overflow-hidden sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <WrenchIcon className="size-5" />
            도구 · 스킬 추가
          </DialogTitle>
          <DialogDescription>
            카탈로그 도구, 등록한 도구, MCP 도구, 스킬을 한 곳에서 관리합니다.
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={(v) => setTab(v as typeof tab)}
          className="flex min-h-0 w-full flex-1 flex-col"
        >
          <TabsList className="w-full">
            <TabsTrigger value="catalog">
              <PackageIcon className="size-3.5" /> Catalog
            </TabsTrigger>
            <TabsTrigger value="tools">
              <WrenchIcon className="size-3.5" /> My Tools
            </TabsTrigger>
            <TabsTrigger value="mcp">
              <ServerIcon className="size-3.5" /> MCP
            </TabsTrigger>
            <TabsTrigger value="skills">
              <SparklesIcon className="size-3.5" /> Skills
            </TabsTrigger>
          </TabsList>

          <TabsContent value="catalog" className="min-w-0 flex-1 overflow-y-auto pt-4">
            <CatalogPanel />
          </TabsContent>

          <TabsContent value="tools" className="min-w-0 flex-1 overflow-y-auto pt-4">
            <ToolsPanel
              allTools={allTools}
              selectedToolIds={selectedToolIds}
              onToggle={onToggleTool}
            />
          </TabsContent>

          <TabsContent value="mcp" className="min-w-0 flex-1 overflow-y-auto pt-4">
            <McpPanel
              selectedIds={selectedMcpToolIds}
              onToggle={onToggleMcpTool}
            />
          </TabsContent>

          <TabsContent value="skills" className="min-w-0 flex-1 overflow-y-auto pt-4">
            <SkillsPanel
              allSkills={allSkills}
              selectedSkillIds={selectedSkillIds}
              onToggle={onToggleSkill}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

// -- Catalog (browse-only, click-to-create flow lands later) ----------------

function CatalogPanel() {
  const { data: definitions } = useCredentialTypes()
  const [query, setQuery] = useState('')

  // The credentials catalog already serves as the inventory of "what types
  // of integrations exist" — we filter to non-LLM since LLM definitions
  // describe model providers, not callable tools.
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

  return (
    <div className="space-y-3">
      <div className="rounded-lg border bg-amber-50/40 p-3 text-xs text-amber-900 dark:border-amber-900/30 dark:bg-amber-950/20 dark:text-amber-200">
        <p>
          카탈로그에서 도구 만들려면{' '}
          <Link href="/tools" className="font-medium underline">
            /tools
          </Link>{' '}
          페이지로 이동하세요. 그곳에서 credential을 묶어 인스턴스를 만들면
          이 다이얼로그의 <strong>My Tools</strong> 탭에 자동으로 표시됩니다.
          <br />
          <span className="text-[11px] opacity-80">
            (TODO: inline 인스턴스 생성 — follow-up)
          </span>
        </p>
      </div>

      <div className="relative">
        <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="카탈로그 검색"
          className="pl-9"
        />
      </div>

      {!definitions ? (
        <Skeleton className="h-32 w-full" />
      ) : filtered.length === 0 ? (
        <EmptyBox>표시할 카탈로그 항목이 없습니다.</EmptyBox>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {filtered.map((d) => (
            <CatalogCard key={d.key} definition={d} />
          ))}
        </div>
      )}
    </div>
  )
}

function CatalogCard({ definition }: { definition: CredentialDefinition }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="flex items-center gap-2">
        <span className="truncate text-sm font-medium">{definition.display_name}</span>
        <Badge variant="secondary" className="text-[10px]">
          {definition.category}
        </Badge>
      </div>
      <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
        {definition.key}
      </p>
    </div>
  )
}

// -- My Tools / Skills 공통 2-column ----------------------------------------

function ToolsPanel({
  allTools,
  selectedToolIds,
  onToggle,
}: {
  allTools: ToolInstance[]
  selectedToolIds: Set<string>
  onToggle: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const selected = allTools.filter((t) => selectedToolIds.has(t.id))
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
    <TwoColumn
      currentLabel="현재 선택"
      current={selected.map((t) => (
        <ToolRow
          key={t.id}
          name={t.name}
          subtitle={t.definition_key}
          onAction={() => onToggle(t.id)}
          actionLabel="제거"
          actionIcon="remove"
        />
      ))}
      availableLabel="추가 가능"
      query={query}
      onQueryChange={setQuery}
      available={available.map((t) => (
        <ToolRow
          key={t.id}
          name={t.name}
          subtitle={t.definition_key}
          description={t.description}
          onAction={() => onToggle(t.id)}
          actionLabel="추가"
          actionIcon="add"
        />
      ))}
      emptyAvailable={
        allTools.length === 0
          ? '아직 등록한 도구가 없습니다. /tools에서 인스턴스를 만들어주세요.'
          : '검색 결과가 없습니다.'
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
  const { data: tools, isLoading } = useAllMcpTools()
  const [query, setQuery] = useState('')

  const list = useMemo<McpToolWithServer[]>(() => tools ?? [], [tools])
  const selected = list.filter((t) => selectedIds.has(t.id))
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
    <TwoColumn
      currentLabel="현재 선택"
      current={selected.map((t) => (
        <ToolRow
          key={t.id}
          name={t.name}
          subtitle={`${t.server_name} · MCP`}
          onAction={() => onToggle(t.id)}
          actionLabel="제거"
          actionIcon="remove"
        />
      ))}
      availableLabel="추가 가능 (MCP)"
      query={query}
      onQueryChange={setQuery}
      available={available.map((t) => (
        <ToolRow
          key={t.id}
          name={t.name}
          subtitle={`${t.server_name} · MCP`}
          description={t.description}
          onAction={() => onToggle(t.id)}
          actionLabel="추가"
          actionIcon="add"
        />
      ))}
      emptyAvailable={
        list.length === 0
          ? 'MCP 도구가 없습니다. /mcp-servers에서 서버를 추가하면 자동으로 표시됩니다.'
          : '검색 결과가 없습니다.'
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
  const [query, setQuery] = useState('')
  const selected = allSkills.filter((s) => selectedSkillIds.has(s.id))
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
    <TwoColumn
      currentLabel="현재 선택"
      current={selected.map((s) => (
        <ToolRow
          key={s.id}
          name={s.name}
          subtitle="Skill"
          onAction={() => onToggle(s.id)}
          actionLabel="제거"
          actionIcon="remove"
        />
      ))}
      availableLabel="추가 가능"
      query={query}
      onQueryChange={setQuery}
      available={available.map((s) => (
        <ToolRow
          key={s.id}
          name={s.name}
          subtitle="Skill"
          description={s.description}
          onAction={() => onToggle(s.id)}
          actionLabel="추가"
          actionIcon="add"
        />
      ))}
      emptyAvailable={
        allSkills.length === 0
          ? '아직 등록한 스킬이 없습니다. /skills에서 업로드해주세요.'
          : '검색 결과가 없습니다.'
      }
    />
  )
}

// -- Shared shells ----------------------------------------------------------

function TwoColumn({
  currentLabel,
  current,
  availableLabel,
  query,
  onQueryChange,
  available,
  emptyAvailable,
}: {
  currentLabel: string
  current: React.ReactNode[]
  availableLabel: string
  query: string
  onQueryChange: (v: string) => void
  available: React.ReactNode[]
  emptyAvailable: string
}) {
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <section className="flex min-h-0 flex-col">
        <h3 className="mb-3 text-sm font-medium">
          {currentLabel} ({current.length})
        </h3>
        <div className="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
          {current.length === 0 ? (
            <EmptyBox>선택된 항목이 없습니다.</EmptyBox>
          ) : (
            current
          )}
        </div>
      </section>
      <section className="flex min-h-0 flex-col">
        <h3 className="mb-3 text-sm font-medium">{availableLabel}</h3>
        <div className="relative mb-3">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="검색"
            className="pl-9"
          />
        </div>
        <div className="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
          {available.length === 0 ? <EmptyBox>{emptyAvailable}</EmptyBox> : available}
        </div>
      </section>
    </div>
  )
}

function ToolRow({
  name,
  subtitle,
  description,
  onAction,
  actionLabel,
  actionIcon,
}: {
  name: string
  subtitle: string
  description?: string | null
  onAction: () => void
  actionLabel: string
  actionIcon: 'add' | 'remove'
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg border p-3">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="truncate text-[11px] text-muted-foreground">{subtitle}</p>
        {description && (
          <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant={actionIcon === 'add' ? 'outline' : 'ghost'}
        onClick={onAction}
        className="shrink-0"
      >
        {actionIcon === 'add' ? (
          <PlusIcon className="size-3.5" />
        ) : (
          <Trash2Icon className="size-3.5" />
        )}
        {actionLabel}
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
