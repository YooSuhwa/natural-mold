'use client'

import { PublishWizard } from '@/components/marketplace/publish-wizard'
import { SkillCreateDialog } from '@/components/skill/skill-create-dialog'
import type { Skill } from '@/lib/types/skill'

type CreateTab = 'chat' | 'text' | 'package'

type SkillPageDialogsProps = {
  readonly createOpen: boolean
  readonly createTab: CreateTab
  readonly publishSkill: Skill | null
  readonly onCreateOpenChange: (open: boolean) => void
  readonly onPublishOpenChange: (open: boolean) => void
  readonly onCreated: (id: string) => void
  readonly onStartChat: (request: string) => void
}

export function SkillPageDialogs({
  createOpen,
  createTab,
  publishSkill,
  onCreateOpenChange,
  onPublishOpenChange,
  onCreated,
  onStartChat,
}: SkillPageDialogsProps) {
  return (
    <>
      <SkillCreateDialog
        key={`create-${createTab}`}
        open={createOpen}
        onOpenChange={onCreateOpenChange}
        initialTab={createTab}
        onCreated={(id) => onCreated(id)}
        onStartChat={onStartChat}
      />
      <PublishWizard
        skill={publishSkill}
        open={!!publishSkill}
        onOpenChange={onPublishOpenChange}
      />
    </>
  )
}
