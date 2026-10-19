'use client'

import { useTranslations } from 'next-intl'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'

export interface TriggerDeleteTarget {
  id: string
  description: string
}

interface SettingsConfirmDialogsProps {
  deleteAgentOpen: boolean
  onDeleteAgentOpenChange: (open: boolean) => void
  deletingAgent: boolean
  onConfirmDeleteAgent: () => void
  triggerTarget: TriggerDeleteTarget | null
  onTriggerTargetChange: (target: TriggerDeleteTarget | null) => void
  deletingTrigger: boolean
  onConfirmDeleteTrigger: (id: string) => void
}

/** Agent and trigger delete confirmations of the settings screen. */
export function SettingsConfirmDialogs({
  deleteAgentOpen,
  onDeleteAgentOpenChange,
  deletingAgent,
  onConfirmDeleteAgent,
  triggerTarget,
  onTriggerTargetChange,
  deletingTrigger,
  onConfirmDeleteTrigger,
}: SettingsConfirmDialogsProps) {
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')
  return (
    <>
      <DeleteConfirmDialog
        open={deleteAgentOpen}
        onOpenChange={onDeleteAgentOpenChange}
        title={t('deleteDialog.title')}
        description={t('deleteDialog.description')}
        cancelLabel={tc('cancel')}
        confirmLabel={tc('delete')}
        isPending={deletingAgent}
        onConfirm={onConfirmDeleteAgent}
      />
      <DeleteConfirmDialog
        open={!!triggerTarget}
        onOpenChange={(v) => !v && onTriggerTargetChange(null)}
        title={t('trigger.deleteConfirm')}
        description={triggerTarget?.description ?? ''}
        cancelLabel={tc('cancel')}
        confirmLabel={tc('delete')}
        isPending={deletingTrigger}
        onConfirm={() => {
          if (triggerTarget) {
            onConfirmDeleteTrigger(triggerTarget.id)
            onTriggerTargetChange(null)
          }
        }}
      />
    </>
  )
}
