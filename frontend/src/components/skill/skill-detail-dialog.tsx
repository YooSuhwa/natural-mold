'use client'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DomainIconTile, getDomainIconIdForSkillKind } from '@/components/shared/icon'
import { useSkill } from '@/lib/hooks/use-skills'
import { useTranslations } from 'next-intl'

import { PackageSkillEditor } from './skill-detail-package-editor'
import { TextSkillEditor } from './skill-detail-text-editor'

interface Props {
  skillId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SkillDetailDialog({ skillId, open, onOpenChange }: Props) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="tall">
      {skillId ? (
        <SkillDetailBody key={skillId} skillId={skillId} onClose={() => onOpenChange(false)} />
      ) : (
        <SkillDetailLoading onClose={() => onOpenChange(false)} />
      )}
    </DialogShell>
  )
}

function SkillDetailBody({ skillId, onClose }: { skillId: string; onClose: () => void }) {
  const t = useTranslations('skill.detailDialog')
  const { data: skill } = useSkill(skillId)

  if (!skill) {
    return <SkillDetailLoading onClose={onClose} />
  }

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
    />
  )

  if (skill.kind === 'text') {
    return (
      <>
        {header}
        <TextSkillEditor skillId={skillId} onClose={onClose} />
      </>
    )
  }

  if (skill.kind === 'package') {
    return (
      <>
        {header}
        <PackageSkillEditor skillId={skillId} onClose={onClose} />
      </>
    )
  }

  return (
    <>
      {header}
      <DialogShell.Body>
        <p className="text-sm text-muted-foreground">{t('unsupported')}</p>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('close')}
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
