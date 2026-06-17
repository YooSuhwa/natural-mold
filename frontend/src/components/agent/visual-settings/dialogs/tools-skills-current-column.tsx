'use client'

import type { ReactNode } from 'react'
import { Trash2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { SkillQualityInline } from '@/components/skill/skill-quality-inline'
import { Button } from '@/components/ui/button'
import type { McpToolWithServer } from '@/lib/types/mcp'
import type { Skill } from '@/lib/types/skill'
import type { ToolInstance } from '@/lib/types/tool'

import type { SelectedKind } from './tools-skills-dialog-types'
import { KindIcon } from './tools-skills-kind-icon'
import { EmptyBox } from './tools-skills-list'

export function CurrentColumn({
  total,
  tools,
  mcpTools,
  skills,
  onRemoveTool,
  onRemoveMcp,
  onRemoveSkill,
}: {
  readonly total: number
  readonly tools: readonly ToolInstance[]
  readonly mcpTools: readonly McpToolWithServer[]
  readonly skills: readonly Skill[]
  readonly onRemoveTool: (id: string) => void
  readonly onRemoveMcp: (id: string) => void
  readonly onRemoveSkill: (id: string) => void
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
            {tools.map((tool) => (
              <SelectedRow
                key={`tool-${tool.id}`}
                kind="tool"
                name={tool.name}
                subtitle={tool.definition_key}
                onRemove={() => onRemoveTool(tool.id)}
              />
            ))}
            {mcpTools.map((tool) => (
              <SelectedRow
                key={`mcp-${tool.id}`}
                kind="mcp"
                name={tool.name}
                subtitle={`${tool.server_name} · MCP`}
                onRemove={() => onRemoveMcp(tool.id)}
              />
            ))}
            {skills.map((skill) => (
              <SelectedRow
                key={`skill-${skill.id}`}
                kind="skill"
                name={skill.name}
                subtitle={t('kind.skill')}
                quality={<SkillQualityInline skill={skill} />}
                onRemove={() => onRemoveSkill(skill.id)}
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
  quality,
  onRemove,
}: {
  readonly kind: SelectedKind
  readonly name: string
  readonly subtitle: string
  readonly quality?: ReactNode
  readonly onRemove: () => void
}) {
  const t = useTranslations('agent.visualSettings.toolsSkillsDialog')

  return (
    <div className="flex items-center gap-3 rounded-lg border p-3">
      <KindIcon kind={kind} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="truncate moldy-ui-caption text-muted-foreground">{subtitle}</p>
        {quality ? <div className="mt-1 flex flex-wrap gap-1">{quality}</div> : null}
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
