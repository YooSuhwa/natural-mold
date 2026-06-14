'use client'

import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { DomainIconTile, getDomainIconIdForSkillKind } from '@/components/shared/icon'
import { Tabs } from '@/components/ui/tabs'
import { useSkill } from '@/lib/hooks/use-skills'
import type { Skill } from '@/lib/types/skill'

import { SkillCredentialsTab } from './skill-credentials-tab'
import { PackageSkillEditor } from './skill-detail-package-editor'
import {
  coerceSkillDetailTab,
  getVisibleSkillDetailTabs,
  SkillDetailTabs,
  type SkillDetailTab,
} from './skill-detail-tabs'
import { SkillEvaluationTab } from './skill-evaluation-tab'
import { SkillHistoryTab } from './skill-history-tab'
import { SkillMetadataTab } from './skill-metadata-tab'
import { SkillSummaryStrip } from './skill-summary-strip'
import { TextSkillEditor } from './skill-detail-text-editor'

interface Props {
  skillId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  initialTab?: SkillDetailTab
  onTabChange?: (tab: SkillDetailTab) => void
  onImprove?: (skillId: string) => void
}

export function SkillDetailDialog({
  skillId,
  open,
  onOpenChange,
  initialTab = 'content',
  onTabChange,
  onImprove,
}: Props) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="tall">
      {skillId ? (
        <SkillDetailBody
          key={skillId}
          skillId={skillId}
          initialTab={initialTab}
          onTabChange={onTabChange}
          onImprove={onImprove}
          onClose={() => onOpenChange(false)}
        />
      ) : (
        <SkillDetailLoading onClose={() => onOpenChange(false)} />
      )}
    </DialogShell>
  )
}

function SkillDetailBody({
  skillId,
  initialTab,
  onTabChange,
  onImprove,
  onClose,
}: {
  readonly skillId: string
  readonly initialTab: SkillDetailTab
  readonly onTabChange?: (tab: SkillDetailTab) => void
  readonly onImprove?: (skillId: string) => void
  readonly onClose: () => void
}) {
  const t = useTranslations('skill.detailDialog')
  const { data: skill } = useSkill(skillId)
  const [activeTab, setActiveTab] = useState<SkillDetailTab>(initialTab)

  if (!skill) {
    return <SkillDetailLoading onClose={onClose} />
  }

  function handleTabChange(value: string) {
    const next = coerceSkillDetailTab(value)
    setActiveTab(next)
    onTabChange?.(next)
  }

  function handleOpenCredentials() {
    setActiveTab('credentials')
    onTabChange?.('credentials')
  }

  const visibleTabs = getVisibleSkillDetailTabs(skill, activeTab)

  const header = (
    <DialogShell.Header
      icon={
        <DomainIconTile
          iconId={getDomainIconIdForSkillKind(skill.kind)}
          className="size-9"
          iconClassName="size-5"
        />
      }
      title={
        <span className="inline-flex items-center gap-2">
          {skill.name}
          <Badge variant="secondary" className="moldy-ui-micro">
            {skill.kind}
          </Badge>
        </span>
      }
      description={skill.description ?? skill.slug}
      actions={
        <div className="flex flex-wrap items-center justify-end gap-2">
          <SkillSummaryStrip skill={skill} />
          {onImprove ? (
            <Button type="button" variant="outline" size="sm" onClick={() => onImprove(skill.id)}>
              <Sparkles className="size-3.5" />
              {t('improveByChat')}
            </Button>
          ) : null}
        </div>
      }
    />
  )

  return (
    <>
      {header}
      <Tabs value={activeTab} onValueChange={handleTabChange} className="min-h-0 flex-1 gap-0">
        <SkillDetailTabs visibleTabs={visibleTabs} />
        {renderSkillDetailTab({
          activeTab,
          skillId,
          skill,
          onClose,
          onOpenCredentials: handleOpenCredentials,
          unsupported: t('unsupported'),
          closeLabel: t('close'),
        })}
      </Tabs>
    </>
  )
}

function renderSkillDetailTab({
  activeTab,
  skillId,
  skill,
  onClose,
  onOpenCredentials,
  unsupported,
  closeLabel,
}: {
  readonly activeTab: SkillDetailTab
  readonly skillId: string
  readonly skill: Skill
  readonly onClose: () => void
  readonly onOpenCredentials: () => void
  readonly unsupported: string
  readonly closeLabel: string
}) {
  if (activeTab === 'credentials')
    return <SkillCredentialsTab skillId={skillId} onClose={onClose} />
  if (activeTab === 'evaluation') {
    return (
      <>
        <DialogShell.Body>
          <SkillEvaluationTab
            skillId={skillId}
            skillContentHash={skill.content_hash}
            needsCredentialSetup={skill.health?.state === 'needs_credentials'}
            onOpenCredentials={onOpenCredentials}
          />
        </DialogShell.Body>
        <DialogShell.Footer>
          <Button variant="outline" onClick={onClose}>
            {closeLabel}
          </Button>
        </DialogShell.Footer>
      </>
    )
  }
  if (activeTab === 'history') return <SkillHistoryTab skillId={skillId} onClose={onClose} />
  if (activeTab === 'metadata') return <SkillMetadataTab skill={skill} onClose={onClose} />
  if (skill.kind === 'text') {
    return <TextSkillEditor skillId={skillId} onClose={onClose} showCredentials={false} />
  }
  if (skill.kind === 'package') {
    return <PackageSkillEditor skillId={skillId} onClose={onClose} showCredentials={false} />
  }
  return (
    <>
      <DialogShell.Body>
        <p className="text-sm text-muted-foreground">{unsupported}</p>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {closeLabel}
        </Button>
      </DialogShell.Footer>
    </>
  )
}

function SkillDetailLoading({ onClose }: { readonly onClose: () => void }) {
  const t = useTranslations('skill.detailDialog')

  return (
    <>
      <DialogShell.Header title={t('loading')} />
      <DialogShell.Body>
        <Skeleton className="h-40 w-full rounded-lg" />
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}
