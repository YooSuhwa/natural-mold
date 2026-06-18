'use client'

import { useState, useCallback } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { ScheduleDialog } from '@/features/schedules/components/schedule-dialog'
import { ScheduleListCard } from '@/features/schedules/components/schedule-list-card'
import { formatTriggerSummary } from '@/features/schedules/lib/cron-labels'
import type { AgentTrigger, TriggerCreateRequest, TriggerUpdateRequest } from '@/lib/types'

export interface ScheduleNodeData {
  triggers: AgentTrigger[]
  agentId: string
  onCreateTrigger: (data: TriggerCreateRequest) => void
  onUpdateTrigger: (triggerId: string, data: TriggerUpdateRequest) => void
  onDeleteTrigger: (triggerId: string) => void
  isPending?: boolean
}

function useFormatTriggerSummary() {
  const t = useTranslations('agent.schedule')

  return useCallback(
    (trigger: AgentTrigger): string =>
      formatTriggerSummary(trigger, {
        everyNMin: (mins) => t('everyNMin', { mins }),
        atTimeDays: (time, days) => t('atTimeDays', { time, days }),
        atTimeDay: (time, day) => t('atTimeDay', { time, day }),
        atTimeEveryDay: (time) => t('atTimeEveryDay', { time }),
        weekdays: {
          '0': t('weekdays.sun'),
          '1': t('weekdays.mon'),
          '2': t('weekdays.tue'),
          '3': t('weekdays.wed'),
          '4': t('weekdays.thu'),
          '5': t('weekdays.fri'),
          '6': t('weekdays.sat'),
        },
      }),
    [t],
  )
}

export function ScheduleNode({ data }: NodeProps) {
  const t = useTranslations('agent.schedule')
  const tSettings = useTranslations('agent.settings')
  const formatTriggerSummary = useFormatTriggerSummary()

  const {
    triggers = [],
    agentId = '',
    onCreateTrigger = () => {},
    onUpdateTrigger = () => {},
    onDeleteTrigger = () => {},
    isPending = false,
  } = (data ?? {}) as Partial<ScheduleNodeData>

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTrigger, setEditingTrigger] = useState<AgentTrigger | null>(null)

  function handleOpenCreate() {
    setEditingTrigger(null)
    setDialogOpen(true)
  }

  function handleOpenEdit(trigger: AgentTrigger) {
    setEditingTrigger(trigger)
    setDialogOpen(true)
  }

  function handleSubmit(
    payload: TriggerCreateRequest | { triggerId: string; data: TriggerUpdateRequest },
  ) {
    if ('triggerId' in payload) {
      onUpdateTrigger(payload.triggerId, payload.data)
    } else {
      onCreateTrigger(payload)
    }
    setDialogOpen(false)
    setEditingTrigger(null)
  }

  return (
    <>
      <div className="moldy-flow-node w-56 nowheel">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
            {t('title')}
          </span>
          <Button variant="ghost" size="xs" onClick={handleOpenCreate}>
            <PlusIcon className="size-3" />
            {t('add')}
          </Button>
        </div>

        {/* Content */}
        <div>
          {triggers.length === 0 ? (
            <div className="px-3 py-4 text-center text-xs text-muted-foreground">
              {t('noSchedules')}
            </div>
          ) : (
            <div className="max-h-52 space-y-1 overflow-y-auto p-1.5">
              {triggers.map((trigger) => (
                <ScheduleListCard
                  key={trigger.id}
                  trigger={trigger}
                  summary={formatTriggerSummary(trigger)}
                  editLabel={tSettings('trigger.edit')}
                  deleteLabel={tSettings('trigger.delete')}
                  onEdit={handleOpenEdit}
                  onDelete={onDeleteTrigger}
                />
              ))}
            </div>
          )}
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="moldy-flow-handle moldy-flow-handle-schedule"
      />
      <ScheduleDialog
        open={dialogOpen}
        onOpenChange={(v) => {
          setDialogOpen(v)
          if (!v) setEditingTrigger(null)
        }}
        onSubmit={handleSubmit}
        agentId={agentId}
        trigger={editingTrigger}
        isPending={isPending}
      />
    </>
  )
}
