'use client'

import { useState, useCallback } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { ClockIcon, PencilIcon, PlusIcon, TrashIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { ScheduleDialog } from '../dialogs/schedule-dialog'
import type { AgentTrigger, TriggerCreateRequest, TriggerUpdateRequest } from '@/lib/types'

export interface ScheduleNodeData {
  triggers: AgentTrigger[]
  onCreateTrigger: (data: TriggerCreateRequest) => void
  onUpdateTrigger: (triggerId: string, data: TriggerUpdateRequest) => void
  onDeleteTrigger: (triggerId: string) => void
  isPending?: boolean
}

function useFormatTriggerSummary() {
  const t = useTranslations('agent.schedule')

  return useCallback(
    (trigger: AgentTrigger): string => {
      if (trigger.trigger_type === 'interval') {
        const mins = trigger.schedule_config.interval_minutes ?? 10
        return t('everyNMin', { mins })
      }

      const cron = trigger.schedule_config.cron_expression ?? ''
      const parts = cron.split(' ')
      if (parts.length !== 5) return cron

      const [min, hour, dom, , dow] = parts
      const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`

      if (dow !== '*') {
        const dayMap: Record<string, string> = {
          '0': t('weekdays.sun'),
          '1': t('weekdays.mon'),
          '2': t('weekdays.tue'),
          '3': t('weekdays.wed'),
          '4': t('weekdays.thu'),
          '5': t('weekdays.fri'),
          '6': t('weekdays.sat'),
        }
        const days = dow
          .split(',')
          .map((d) => dayMap[d] ?? d)
          .join(', ')
        return t('atTimeDays', { time, days })
      }

      if (dom !== '*') {
        return t('atTimeDay', { time, day: dom })
      }

      return t('atTimeEveryDay', { time })
    },
    [t],
  )
}

export function ScheduleNode({ data }: NodeProps) {
  const t = useTranslations('agent.schedule')
  const formatTriggerSummary = useFormatTriggerSummary()

  const {
    triggers = [],
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
      <div className="w-[220px] rounded-xl border bg-card shadow-md nowheel">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
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
            <div className="max-h-[200px] overflow-y-auto p-1.5 space-y-1">
              {triggers.map((trigger) => (
                <div
                  key={trigger.id}
                  className="group flex items-center gap-1.5 rounded-md px-1.5 py-1 hover:bg-muted/50"
                >
                  <ClockIcon className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate text-[11px] text-muted-foreground">
                    {formatTriggerSummary(trigger)}
                  </span>
                  <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <button
                      onClick={() => handleOpenEdit(trigger)}
                      className="rounded p-0.5 text-muted-foreground hover:text-foreground"
                    >
                      <PencilIcon className="size-3" />
                    </button>
                    <button
                      onClick={() => onDeleteTrigger(trigger.id)}
                      className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                    >
                      <TrashIcon className="size-3" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-amber-500 !w-2.5 !h-2.5" />
      <ScheduleDialog
        open={dialogOpen}
        onOpenChange={(v) => {
          setDialogOpen(v)
          if (!v) setEditingTrigger(null)
        }}
        onSubmit={handleSubmit}
        trigger={editingTrigger}
        isPending={isPending}
      />
    </>
  )
}
