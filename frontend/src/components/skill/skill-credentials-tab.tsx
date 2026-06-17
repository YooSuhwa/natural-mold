'use client'

import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'

import { SkillCredentialBindingsPanel } from './skill-credential-bindings-panel'
import type { SkillDetailTabRender } from './skill-detail-tab-shell'

export function SkillCredentialsTab({
  children,
  skillId,
  onClose,
}: {
  readonly children: SkillDetailTabRender
  readonly skillId: string
  readonly onClose: () => void
}) {
  const t = useTranslations('skill.detailDialog')

  return children({
    body: (
      <>
        <SkillCredentialBindingsPanel
          skillId={skillId}
          emptyFallback={
            <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
              {t('credentialsEmpty')}
            </div>
          }
        />
      </>
    ),
    footer: (
      <>
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
      </>
    ),
  })
}
