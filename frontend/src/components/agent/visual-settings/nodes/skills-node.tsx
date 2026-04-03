'use client'

import { useState } from 'react'
import { Handle, Position } from '@xyflow/react'
import { PlusIcon, TrashIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { AddSkillsDialog } from '../dialogs/add-skills-dialog'
import type { Skill } from '@/lib/types'

export interface SkillsNodeData {
  allSkills: Skill[]
  selectedSkillIds: Set<string>
  onToggleSkill: (skillId: string) => void
  [key: string]: unknown
}

export function SkillsNode({ data }: { data: SkillsNodeData }) {
  const t = useTranslations('agent.visualSettings')
  const [dialogOpen, setDialogOpen] = useState(false)

  const { allSkills = [], selectedSkillIds, onToggleSkill } = data
  const skillIds = selectedSkillIds instanceof Set ? selectedSkillIds : new Set<string>()
  const selectedSkills = allSkills.filter((skill) => skillIds.has(skill.id))

  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-emerald-500 !w-2.5 !h-2.5" />
      <div className="nowheel w-[220px] rounded-xl border bg-card shadow-md">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('nodes.skills')}
          </span>
          <div className="flex items-center gap-0.5">
            <Button variant="ghost" size="icon-xs" onClick={() => setDialogOpen(true)}>
              <PlusIcon className="size-3" />
            </Button>
            <Button variant="ghost" size="xs" disabled className="text-[10px] opacity-40">
              {t('skills.create')}
            </Button>
          </div>
        </div>

        {/* Content */}
        <div className="px-1 py-1">
          {selectedSkills.length === 0 ? (
            <p className="px-2 py-2 text-xs text-muted-foreground">{t('skills.empty')}</p>
          ) : (
            <div className="max-h-[160px] overflow-y-auto">
              {selectedSkills.map((skill) => (
                <div
                  key={skill.id}
                  className="group flex items-center justify-between rounded-md px-2 py-1 hover:bg-muted/50"
                >
                  <span className="truncate text-xs">{skill.name}</span>
                  <button
                    onClick={() => onToggleSkill(skill.id)}
                    className="invisible shrink-0 p-0.5 text-muted-foreground hover:text-destructive group-hover:visible"
                    aria-label={t('skills.remove')}
                  >
                    <TrashIcon className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <AddSkillsDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        allSkills={allSkills}
        selectedSkillIds={skillIds}
        onToggleSkill={onToggleSkill}
      />
    </>
  )
}
