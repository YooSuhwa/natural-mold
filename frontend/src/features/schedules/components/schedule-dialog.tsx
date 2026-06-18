'use client'

import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import type { AgentTrigger, TriggerCreateRequest, TriggerUpdateRequest } from '@/lib/types'
import { ScheduleForm } from './schedule-form'

interface ScheduleDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: TriggerCreateRequest | { triggerId: string; data: TriggerUpdateRequest }) => void
  trigger?: AgentTrigger | null
  agentId?: string
  isPending?: boolean
}

export function ScheduleDialog({
  open,
  onOpenChange,
  onSubmit,
  trigger,
  agentId,
  isPending,
}: ScheduleDialogProps) {
  const t = useTranslations('agent.schedule')
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="fixed">
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body className="flex min-h-0 flex-1 space-y-0 overflow-hidden p-0">
        <ScheduleForm
          agentId={agentId}
          trigger={trigger}
          isEdit={!!trigger}
          isPending={isPending}
          onCancel={() => onOpenChange(false)}
          onSubmit={(req) => {
            if (trigger) onSubmit({ triggerId: trigger.id, data: req })
            else onSubmit(req)
          }}
        />
      </DialogShell.Body>
    </DialogShell>
  )
}
