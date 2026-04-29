'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'

interface ToolsSkillsDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  selectedToolIds: Set<string>
  onToggleTool: (id: string) => void
  selectedSkillIds: Set<string>
  onToggleSkill: (id: string) => void
}

export function ToolsSkillsDialog({
  open,
  onOpenChange,
  selectedToolIds,
  onToggleTool,
  selectedSkillIds,
  onToggleSkill,
}: ToolsSkillsDialogProps) {
  const t = useTranslations('agent.settings')
  const ts = useTranslations('agent.settings.toolsSkills')
  const tc = useTranslations('common')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{ts('dialogTitle')}</DialogTitle>
        </DialogHeader>

        <Tabs defaultValue="tools">
          <TabsList>
            <TabsTrigger value="tools">{ts('toolsTab')}</TabsTrigger>
            <TabsTrigger value="skills">{ts('skillsTab')}</TabsTrigger>
          </TabsList>

          <TabsContent value="tools" className="max-h-96 overflow-y-auto py-2">
            <ToolsTabPanel
              selectedToolIds={selectedToolIds}
              onToggleTool={onToggleTool}
              noToolsTemplate={String(t.raw('noTools'))}
              toolsLinkLabel={t('toolsLink')}
            />
          </TabsContent>

          <TabsContent value="skills" className="max-h-96 overflow-y-auto py-2">
            <SkillsTabPanel
              selectedSkillIds={selectedSkillIds}
              onToggleSkill={onToggleSkill}
              noSkillsTemplate={String(ts.raw('noSkills'))}
              skillsLinkLabel={ts('skillsLink')}
            />
          </TabsContent>
        </Tabs>

        <DialogFooter>
          <DialogClose render={<Button>{tc('done')}</Button>} />
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ToolsTabPanel({
  selectedToolIds,
  onToggleTool,
  noToolsTemplate,
  toolsLinkLabel,
}: {
  selectedToolIds: Set<string>
  onToggleTool: (id: string) => void
  noToolsTemplate: string
  toolsLinkLabel: string
}) {
  const { data: tools } = useTools()
  const noToolsParts = noToolsTemplate.split('{link}')

  if (!tools) {
    return <Skeleton className="h-24 w-full" />
  }

  if (tools.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {noToolsParts[0]}
        <Link href="/tools" className="text-primary hover:underline">
          {toolsLinkLabel}
        </Link>
        {noToolsParts[1]}
      </p>
    )
  }

  return (
    <div className="space-y-2 rounded-lg border p-3">
      {tools.map((tool) => (
        <label key={tool.id} className="flex cursor-pointer items-center gap-3 text-sm">
          <Checkbox
            checked={selectedToolIds.has(tool.id)}
            onCheckedChange={() => onToggleTool(tool.id)}
          />
          <span className="font-medium">{tool.name}</span>
          {tool.description && (
            <span className="truncate text-xs text-muted-foreground">- {tool.description}</span>
          )}
        </label>
      ))}
    </div>
  )
}

function SkillsTabPanel({
  selectedSkillIds,
  onToggleSkill,
  noSkillsTemplate,
  skillsLinkLabel,
}: {
  selectedSkillIds: Set<string>
  onToggleSkill: (id: string) => void
  noSkillsTemplate: string
  skillsLinkLabel: string
}) {
  const { data: skills } = useSkills()
  const noSkillsParts = noSkillsTemplate.split('{link}')

  if (!skills) {
    return <Skeleton className="h-24 w-full" />
  }

  if (skills.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {noSkillsParts[0]}
        <Link href="/skills" className="text-primary hover:underline">
          {skillsLinkLabel}
        </Link>
        {noSkillsParts[1]}
      </p>
    )
  }

  return (
    <div className="space-y-2 rounded-lg border p-3">
      {skills.map((skill) => (
        <label key={skill.id} className="flex cursor-pointer items-center gap-3 text-sm">
          <Checkbox
            checked={selectedSkillIds.has(skill.id)}
            onCheckedChange={() => onToggleSkill(skill.id)}
          />
          <span className="font-medium">{skill.name}</span>
          {skill.description && (
            <span className="truncate text-xs text-muted-foreground">- {skill.description}</span>
          )}
        </label>
      ))}
    </div>
  )
}
