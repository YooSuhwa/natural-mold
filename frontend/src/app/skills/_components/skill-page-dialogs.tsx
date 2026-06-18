'use client'

import { PublishWizard } from '@/components/marketplace/publish-wizard'
import { SkillBuilderDialog } from '@/components/skill/skill-builder-dialog'
import { SkillCreateDialog } from '@/components/skill/skill-create-dialog'
import { SkillDetailDialog } from '@/components/skill/skill-detail-dialog'
import type { SkillDetailTab } from '@/components/skill/skill-detail-tabs'
import type { Skill } from '@/lib/types/skill'

type CreateTab = 'chat' | 'text' | 'package'
type BuilderMode = 'create' | 'improve'

type SkillPageDialogsProps = {
  readonly createOpen: boolean
  readonly createTab: CreateTab
  readonly builderOpen: boolean
  readonly builderMode: BuilderMode
  readonly builderSourceSkillId: string | null
  readonly builderInitialRequest: string
  readonly detailId: string | null
  readonly detailTab: SkillDetailTab
  readonly publishSkill: Skill | null
  readonly onCreateOpenChange: (open: boolean) => void
  readonly onBuilderOpenChange: (open: boolean) => void
  readonly onDetailOpenChange: (open: boolean) => void
  readonly onPublishOpenChange: (open: boolean) => void
  readonly onCreated: (id: string, tab?: SkillDetailTab) => void
  readonly onStartChat: (request: string) => void
  readonly onDetailTabChange: (tab: SkillDetailTab) => void
  readonly onImprove: (skillId: string) => void
}

export function SkillPageDialogs({
  createOpen,
  createTab,
  builderOpen,
  builderMode,
  builderSourceSkillId,
  builderInitialRequest,
  detailId,
  detailTab,
  publishSkill,
  onCreateOpenChange,
  onBuilderOpenChange,
  onDetailOpenChange,
  onPublishOpenChange,
  onCreated,
  onStartChat,
  onDetailTabChange,
  onImprove,
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
      <SkillBuilderDialog
        open={builderOpen}
        mode={builderMode}
        sourceSkillId={builderSourceSkillId}
        initialRequest={builderInitialRequest}
        onOpenChange={onBuilderOpenChange}
        onCreated={(id, options) => onCreated(id, options.openTab)}
      />
      <SkillDetailDialog
        skillId={detailId}
        open={!!detailId}
        initialTab={detailTab}
        onTabChange={onDetailTabChange}
        onImprove={onImprove}
        onOpenChange={onDetailOpenChange}
      />
      <PublishWizard
        skill={publishSkill}
        open={!!publishSkill}
        onOpenChange={onPublishOpenChange}
      />
    </>
  )
}
