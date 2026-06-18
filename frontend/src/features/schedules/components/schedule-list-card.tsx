'use client'

import { ClockIcon, PencilIcon, TrashIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { AgentTrigger } from '@/lib/types'

interface ScheduleListCardProps {
  trigger: AgentTrigger
  summary: string
  editLabel: string
  deleteLabel: string
  onEdit: (trigger: AgentTrigger) => void
  onDelete: (triggerId: string) => void
}

export function ScheduleListCard({
  trigger,
  summary,
  editLabel,
  deleteLabel,
  onEdit,
  onDelete,
}: ScheduleListCardProps) {
  return (
    <div className="group flex items-center gap-1.5 rounded-md px-1.5 py-1 hover:bg-muted/50">
      <ClockIcon className="size-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="truncate moldy-ui-caption font-medium">{trigger.name}</div>
        <div className="truncate moldy-ui-micro text-muted-foreground">{summary}</div>
      </div>
      <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={editLabel}
          onClick={() => onEdit(trigger)}
        >
          <PencilIcon className="size-3" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={deleteLabel}
          onClick={() => onDelete(trigger.id)}
        >
          <TrashIcon className="size-3" />
        </Button>
      </div>
    </div>
  )
}
