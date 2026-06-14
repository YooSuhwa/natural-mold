'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { SkillQualityInline } from '@/components/skill/skill-quality-inline'
import { Skeleton } from '@/components/ui/skeleton'
import { useAllMcpTools } from '@/lib/hooks/use-mcp-servers'
import type { McpToolWithServer } from '@/lib/types/mcp'
import type { Skill } from '@/lib/types/skill'
import type { ToolInstance } from '@/lib/types/tool'

import { AvailableList, AvailableRow } from './tools-skills-list'

export function ToolsPanel({
  allTools,
  selectedToolIds,
  onToggle,
}: {
  readonly allTools: readonly ToolInstance[]
  readonly selectedToolIds: ReadonlySet<string>
  readonly onToggle: (id: string) => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const [query, setQuery] = useState('')
  const available = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return allTools
      .filter((tool) => !selectedToolIds.has(tool.id))
      .filter((tool) =>
        !normalizedQuery
          ? true
          : tool.name.toLowerCase().includes(normalizedQuery) ||
            (tool.description ?? '').toLowerCase().includes(normalizedQuery) ||
            tool.definition_key.toLowerCase().includes(normalizedQuery),
      )
  }, [allTools, selectedToolIds, query])

  return (
    <AvailableList
      query={query}
      onQueryChange={setQuery}
      items={available.map((tool) => (
        <AvailableRow
          key={tool.id}
          kind="tool"
          name={tool.name}
          subtitle={tool.definition_key}
          description={tool.description}
          onAdd={() => onToggle(tool.id)}
        />
      ))}
      emptyText={allTools.length === 0 ? t('toolsEmpty') : t('noResults')}
    />
  )
}

export function McpPanel({
  selectedIds,
  onToggle,
}: {
  readonly selectedIds: ReadonlySet<string>
  readonly onToggle: (id: string) => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const { data: tools, isLoading } = useAllMcpTools()
  const [query, setQuery] = useState('')

  const list = useMemo<readonly McpToolWithServer[]>(() => tools ?? [], [tools])
  const available = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return list
      .filter((tool) => !selectedIds.has(tool.id))
      .filter((tool) =>
        !normalizedQuery
          ? true
          : tool.name.toLowerCase().includes(normalizedQuery) ||
            (tool.description ?? '').toLowerCase().includes(normalizedQuery) ||
            tool.server_name.toLowerCase().includes(normalizedQuery),
      )
  }, [list, selectedIds, query])

  if (isLoading) {
    return <Skeleton className="h-40 w-full" />
  }

  return (
    <AvailableList
      query={query}
      onQueryChange={setQuery}
      items={available.map((tool) => (
        <AvailableRow
          key={tool.id}
          kind="mcp"
          name={tool.name}
          subtitle={`${tool.server_name} · MCP`}
          description={tool.description}
          onAdd={() => onToggle(tool.id)}
        />
      ))}
      emptyText={list.length === 0 ? t('mcpEmpty') : t('noResults')}
    />
  )
}

export function SkillsPanel({
  allSkills,
  selectedSkillIds,
  onToggle,
}: {
  readonly allSkills: readonly Skill[]
  readonly selectedSkillIds: ReadonlySet<string>
  readonly onToggle: (id: string) => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')
  const [query, setQuery] = useState('')
  const available = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return allSkills
      .filter((skill) => !selectedSkillIds.has(skill.id))
      .filter((skill) =>
        !normalizedQuery
          ? true
          : skill.name.toLowerCase().includes(normalizedQuery) ||
            (skill.description ?? '').toLowerCase().includes(normalizedQuery),
      )
  }, [allSkills, selectedSkillIds, query])

  return (
    <AvailableList
      query={query}
      onQueryChange={setQuery}
      items={available.map((skill) => (
        <AvailableRow
          key={skill.id}
          kind="skill"
          name={skill.name}
          subtitle={t('kind.skill')}
          quality={<SkillQualityInline skill={skill} />}
          description={skill.description}
          onAdd={() => onToggle(skill.id)}
        />
      ))}
      emptyText={allSkills.length === 0 ? t('skillsEmpty') : t('noResults')}
    />
  )
}
