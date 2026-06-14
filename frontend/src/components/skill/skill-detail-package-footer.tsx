'use client'

import { Loader2, Save, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { Button } from '@/components/ui/button'

type SkillDetailPackageFooterProps = {
  readonly confirmingDelete: boolean
  readonly deletePending: boolean
  readonly savePending: boolean
  readonly saveDisabled: boolean
  readonly sizeBytes: number
  readonly version: string | null
  readonly usedByCount: number
  readonly onAskDelete: () => void
  readonly onCancelDelete: () => void
  readonly onConfirmDelete: () => void
  readonly onClose: () => void
  readonly onSave: () => void
}

export function SkillDetailPackageFooter({
  confirmingDelete,
  deletePending,
  savePending,
  saveDisabled,
  sizeBytes,
  version,
  usedByCount,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
  onClose,
  onSave,
}: SkillDetailPackageFooterProps) {
  const t = useTranslations('skill.detailDialog')

  return (
    <>
      {confirmingDelete ? (
        <div className="flex-1">
          <DeleteConfirmInline
            entity={t('packageEntity')}
            onCancel={onCancelDelete}
            onConfirm={onConfirmDelete}
            pending={deletePending}
          />
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={onAskDelete}
        >
          <Trash2 className="size-3.5" />
          {t('deleteSkill')}
        </Button>
      )}
      <span className="moldy-ui-micro text-muted-foreground">
        {t('usedBy', {
          bytes: sizeBytes,
          version: version ?? '—',
          count: usedByCount,
        })}
      </span>
      <Button variant="outline" onClick={onClose}>
        {t('close')}
      </Button>
      <Button onClick={onSave} disabled={saveDisabled}>
        {savePending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
        {t('saveFile')}
      </Button>
    </>
  )
}
