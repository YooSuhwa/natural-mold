'use client'

import { useState, useMemo, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import {
  PlusIcon,
  Trash2Icon,
  WrenchIcon,
  LayersIcon,
  ServerIcon,
  SparklesIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useAllMcpTools } from '@/lib/hooks/use-mcp-servers'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import { ToolsSkillsDialog } from '../dialogs/tools-skills-dialog'
import { MiddlewaresDialog } from '../dialogs/add-middleware-modal'

interface ToolsMiddlewaresGridProps {
  selectedToolIds: Set<string>
  onToggleTool: (id: string) => void
  selectedMcpToolIds: Set<string>
  onToggleMcpTool: (id: string) => void
  selectedSkillIds: Set<string>
  onToggleSkill: (id: string) => void
  selectedMiddlewareTypes: Set<string>
  onToggleMiddleware: (type: string) => void
}

export function ToolsMiddlewaresGrid({
  selectedToolIds,
  onToggleTool,
  selectedMcpToolIds,
  onToggleMcpTool,
  selectedSkillIds,
  onToggleSkill,
  selectedMiddlewareTypes,
  onToggleMiddleware,
}: ToolsMiddlewaresGridProps) {
  return (
    <div className="grid shrink-0 grid-cols-1 gap-4 md:grid-cols-2">
      <ToolsSkillsBox
        selectedToolIds={selectedToolIds}
        onToggleTool={onToggleTool}
        selectedMcpToolIds={selectedMcpToolIds}
        onToggleMcpTool={onToggleMcpTool}
        selectedSkillIds={selectedSkillIds}
        onToggleSkill={onToggleSkill}
      />
      <MiddlewaresBox
        selectedMiddlewareTypes={selectedMiddlewareTypes}
        onToggleMiddleware={onToggleMiddleware}
      />
    </div>
  )
}

interface ChipItem {
  key: string
  kind: 'tool' | 'mcp' | 'skill'
  name: string
  onRemove: () => void
}

function ToolsSkillsBox({
  selectedToolIds,
  onToggleTool,
  selectedMcpToolIds,
  onToggleMcpTool,
  selectedSkillIds,
  onToggleSkill,
}: {
  selectedToolIds: Set<string>
  onToggleTool: (id: string) => void
  selectedMcpToolIds: Set<string>
  onToggleMcpTool: (id: string) => void
  selectedSkillIds: Set<string>
  onToggleSkill: (id: string) => void
}) {
  const t = useTranslations('agent.settings.toolsSkills')
  const [open, setOpen] = useState(false)
  const { data: tools } = useTools()
  const { data: skills } = useSkills()
  const { data: mcpTools } = useAllMcpTools()

  const isLoading = !tools || !skills || !mcpTools
  const selectedTools = useMemo(
    () => tools?.filter((tool) => selectedToolIds.has(tool.id)) ?? [],
    [tools, selectedToolIds],
  )
  const selectedMcpTools = useMemo(
    () => mcpTools?.filter((mt) => selectedMcpToolIds.has(mt.id)) ?? [],
    [mcpTools, selectedMcpToolIds],
  )
  const selectedSkills = useMemo(
    () => skills?.filter((skill) => selectedSkillIds.has(skill.id)) ?? [],
    [skills, selectedSkillIds],
  )
  const items: ChipItem[] = useMemo(() => {
    const toolItems: ChipItem[] = selectedTools.map((tool) => ({
      key: `tool:${tool.id}`,
      kind: 'tool',
      name: tool.name,
      onRemove: () => onToggleTool(tool.id),
    }))
    const mcpItems: ChipItem[] = selectedMcpTools.map((mt) => ({
      key: `mcp:${mt.id}`,
      kind: 'mcp',
      name: mt.name,
      onRemove: () => onToggleMcpTool(mt.id),
    }))
    const skillItems: ChipItem[] = selectedSkills.map((skill) => ({
      key: `skill:${skill.id}`,
      kind: 'skill',
      name: skill.name,
      onRemove: () => onToggleSkill(skill.id),
    }))
    return [...toolItems, ...mcpItems, ...skillItems]
  }, [
    selectedTools,
    selectedMcpTools,
    selectedSkills,
    onToggleTool,
    onToggleMcpTool,
    onToggleSkill,
  ])

  const isEmpty = items.length === 0

  return (
    <div className="rounded-lg border">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <WrenchIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">{t('title')}</span>
          {!isLoading && !isEmpty && (
            <span className="truncate text-xs text-muted-foreground">
              {t('toolsCount', { n: selectedTools.length })} ·{' '}
              {t('mcpCount', { n: selectedMcpTools.length })} ·{' '}
              {t('skillsCount', { n: selectedSkills.length })}
            </span>
          )}
        </div>
        <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
          <PlusIcon className="size-3.5" />
          {t('addLabel')}
        </Button>
      </div>
      {isLoading ? (
        <div className="p-3">
          <Skeleton className="h-12 w-full" />
        </div>
      ) : isEmpty ? (
        <div className="px-4 py-6 text-center text-sm text-muted-foreground">{t('empty')}</div>
      ) : (
        <div className="max-h-[180px] divide-y overflow-y-auto">
          {items.map((item) => (
            <Row
              key={item.key}
              icon={
                item.kind === 'tool' ? (
                  <WrenchIcon className="moldy-status-accent moldy-status-icon size-3.5" />
                ) : item.kind === 'mcp' ? (
                  <ServerIcon className="moldy-status-info moldy-status-icon size-3.5" />
                ) : (
                  <SparklesIcon className="moldy-status-success moldy-status-icon size-3.5" />
                )
              }
              name={item.name}
              onRemove={item.onRemove}
            />
          ))}
        </div>
      )}
      <ToolsSkillsDialog
        open={open}
        onOpenChange={setOpen}
        allTools={tools ?? []}
        selectedToolIds={selectedToolIds}
        onToggleTool={onToggleTool}
        selectedMcpToolIds={selectedMcpToolIds}
        onToggleMcpTool={onToggleMcpTool}
        allSkills={skills ?? []}
        selectedSkillIds={selectedSkillIds}
        onToggleSkill={onToggleSkill}
        defaultTab="tools"
      />
    </div>
  )
}

function MiddlewaresBox({
  selectedMiddlewareTypes,
  onToggleMiddleware,
}: {
  selectedMiddlewareTypes: Set<string>
  onToggleMiddleware: (type: string) => void
}) {
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)
  const { data: middlewares } = useMiddlewares()

  const selected = middlewares?.filter((mw) => selectedMiddlewareTypes.has(mw.type)) ?? []

  return (
    <div className="rounded-lg border">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-2">
          <LayersIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">{t('middlewares')}</span>
        </div>
        <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
          <PlusIcon className="size-3.5" />
          {tc('add')}
        </Button>
      </div>
      {!middlewares ? (
        <div className="p-3">
          <Skeleton className="h-12 w-full" />
        </div>
      ) : selected.length === 0 ? (
        <div className="px-4 py-6 text-center text-sm text-muted-foreground">
          {t('middlewaresEmpty')}
        </div>
      ) : (
        <div className="max-h-[180px] divide-y overflow-y-auto">
          {selected.map((mw) => (
            <Row
              key={mw.type}
              icon={<LayersIcon className="size-3.5 text-muted-foreground" />}
              name={mw.display_name}
              onRemove={() => onToggleMiddleware(mw.type)}
            />
          ))}
        </div>
      )}
      <MiddlewaresDialog
        open={open}
        onOpenChange={setOpen}
        selectedTypes={selectedMiddlewareTypes}
        onToggleMiddleware={onToggleMiddleware}
      />
    </div>
  )
}

interface RowProps {
  icon: ReactNode
  name: string
  onRemove: () => void
}

function Row({ icon, name, onRemove }: RowProps) {
  const tc = useTranslations('common')
  return (
    <div className="flex items-center justify-between px-4 py-2">
      <div className="flex min-w-0 items-center gap-2">
        {icon}
        <span className="truncate text-sm">{name}</span>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onRemove}
          aria-label={`${tc('remove')} ${name}`}
        >
          <Trash2Icon className="size-3.5" />
        </Button>
      </div>
    </div>
  )
}
