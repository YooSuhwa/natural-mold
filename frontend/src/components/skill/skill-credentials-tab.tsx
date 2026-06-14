'use client'

import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'

import { SkillCredentialBindingsPanel } from './skill-credential-bindings-panel'

export function SkillCredentialsTab({
  skillId,
  onClose,
}: {
  readonly skillId: string
  readonly onClose: () => void
}) {
  const t = useTranslations('skill.detailDialog')

  return (
    <>
      <DialogShell.Body>
        <SkillCredentialBindingsPanel
          skillId={skillId}
          emptyFallback={
            <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
              {t('credentialsEmpty')}
            </div>
          }
        />
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}
