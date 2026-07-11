'use client'

import { Download, Loader2, Save, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { Button } from '@/components/ui/button'

type SkillDetailPackageFooterProps = {
  readonly confirmingDelete: boolean
  readonly deletePending: boolean
  readonly savePending: boolean
  readonly saveDisabled: boolean
  readonly exportHref: string
  readonly sizeBytes: number
  readonly version: string | null
  readonly usedByCount: number
  readonly onAskDelete: () => void
  readonly onCancelDelete: () => void
  readonly onConfirmDelete: () => void
  /** 다이얼로그 전용 닫기 — 풀페이지 스튜디오에서는 생략. */
  readonly onClose?: () => void
  readonly onSave: () => void
  /** 삭제/내보내기/메타 — 스튜디오에서는 설정 탭이 소유하므로 false. */
  readonly showDangerZone?: boolean
}

export function SkillDetailPackageFooter({
  confirmingDelete,
  deletePending,
  savePending,
  saveDisabled,
  exportHref,
  sizeBytes,
  version,
  usedByCount,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
  onClose,
  onSave,
  showDangerZone = true,
}: SkillDetailPackageFooterProps) {
  const t = useTranslations('skill.detailDialog')

  return (
    <>
      {showDangerZone ? (
        confirmingDelete ? (
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
        )
      ) : null}
      {showDangerZone ? (
        <span className="moldy-ui-micro text-muted-foreground">
          {t('usedBy', {
            bytes: sizeBytes,
            version: version ?? '—',
            count: usedByCount,
          })}
        </span>
      ) : null}
      {showDangerZone ? (
        <Button variant="outline" render={<a href={exportHref} download />}>
          <Download className="size-4" />
          {t('exportPackage')}
        </Button>
      ) : null}
      {onClose ? (
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
      ) : null}
      <Button onClick={onSave} disabled={saveDisabled}>
        {savePending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
        {t('saveFile')}
      </Button>
    </>
  )
}
