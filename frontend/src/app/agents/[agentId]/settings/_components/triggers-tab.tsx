import { useState } from 'react'
import Link from 'next/link'
import {
  ExternalLinkIcon,
  Trash2Icon,
  PlayIcon,
  PauseIcon,
  PlusIcon,
  PencilIcon,
} from 'lucide-react'
import { useTranslations, useFormatter } from 'next-intl'
import { useTriggers, useCreateTrigger, useUpdateTrigger } from '@/lib/hooks/use-triggers'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ScheduleDialog } from '@/components/agent/visual-settings/dialogs/schedule-dialog'
import type { AgentTrigger } from '@/lib/types'

interface TriggersTabProps {
  agentId: string
  onRequestDelete: (target: { id: string; description: string }) => void
}

export function TriggersTab({ agentId, onRequestDelete }: TriggersTabProps) {
  const t = useTranslations('agent.settings')
  const format = useFormatter()
  const { data: triggers } = useTriggers(agentId)
  const createTrigger = useCreateTrigger(agentId)
  const updateTrigger = useUpdateTrigger(agentId)

  const [showForm, setShowForm] = useState(false)
  const [editingTrigger, setEditingTrigger] = useState<AgentTrigger | null>(null)

  function statusLabel(status: string) {
    if (status === 'active') return t('trigger.active')
    if (status === 'paused') return t('trigger.paused')
    if (status === 'completed') return t('trigger.completed')
    return t('trigger.error')
  }

  function scheduleSummary(trigger: AgentTrigger) {
    const config = trigger.schedule_config ?? {}
    if (trigger.trigger_type === 'interval') {
      return t('trigger.interval', {
        minutes: config.interval_minutes ?? 10,
      })
    }
    if (trigger.trigger_type === 'one_time') {
      if (!config.scheduled_at) return t('trigger.oneTime')
      return format.dateTime(new Date(config.scheduled_at), {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    }
    return config.cron_expression ?? t('trigger.oneTime')
  }

  return (
    <div className="space-y-3">
      {triggers && triggers.length > 0 ? (
        <div className="space-y-2">
          {triggers.map((trigger) => (
            <Card key={trigger.id}>
              <CardContent className="flex items-center justify-between py-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <Badge
                      variant={
                        trigger.status === 'active'
                          ? 'default'
                          : trigger.status === 'error'
                            ? 'destructive'
                            : 'secondary'
                      }
                    >
                      {statusLabel(trigger.status)}
                    </Badge>
                    <span className="font-medium text-sm">{trigger.name}</span>
                    <span
                      className={trigger.trigger_type === 'cron' ? 'font-mono text-xs' : 'text-sm'}
                    >
                      {scheduleSummary(trigger)}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground truncate max-w-md">
                    &quot;{trigger.input_message}&quot;
                  </p>
                  <div className="flex gap-3 text-xs text-muted-foreground">
                    {trigger.schedule_conversation_id ? (
                      <Link
                        href={`/agents/${agentId}/conversations/${trigger.schedule_conversation_id}`}
                        className="inline-flex items-center gap-1 text-primary-strong hover:underline"
                      >
                        {trigger.schedule_conversation_title ?? t('trigger.resultConversation')}
                        <ExternalLinkIcon className="size-3" />
                        {(trigger.schedule_conversation_unread_count ?? 0) > 0 ? (
                          <Badge variant="secondary">
                            {trigger.schedule_conversation_unread_count}
                          </Badge>
                        ) : null}
                      </Link>
                    ) : (
                      <span>{t('trigger.resultPending')}</span>
                    )}
                    {trigger.last_run_at && (
                      <span>
                        {t('trigger.lastRun', {
                          date: format.dateTime(new Date(trigger.last_run_at), {
                            dateStyle: 'medium',
                            timeStyle: 'short',
                          }),
                        })}
                      </span>
                    )}
                    <span>{t('trigger.runCount', { count: trigger.run_count })}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={
                      trigger.status === 'active' ? t('trigger.pause') : t('trigger.resume')
                    }
                    onClick={() =>
                      updateTrigger.mutate({
                        triggerId: trigger.id,
                        data: {
                          status: trigger.status === 'active' ? 'paused' : 'active',
                        },
                      })
                    }
                  >
                    {trigger.status === 'active' ? (
                      <PauseIcon className="size-4" />
                    ) : (
                      <PlayIcon className="size-4" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t('trigger.edit')}
                    onClick={() => setEditingTrigger(trigger)}
                  >
                    <PencilIcon className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t('trigger.delete')}
                    onClick={() =>
                      onRequestDelete({
                        id: trigger.id,
                        description: `${trigger.name} · ${scheduleSummary(trigger)}`,
                      })
                    }
                  >
                    <Trash2Icon className="size-4 text-muted-foreground" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}

      <Button variant="outline" size="sm" onClick={() => setShowForm(true)}>
        <PlusIcon className="size-4" />
        {t('trigger.addNew')}
      </Button>
      <ScheduleDialog
        open={showForm || !!editingTrigger}
        onOpenChange={(open) => {
          if (!open) {
            setShowForm(false)
            setEditingTrigger(null)
          }
        }}
        agentId={agentId}
        trigger={editingTrigger}
        isPending={createTrigger.isPending || updateTrigger.isPending}
        onSubmit={async (payload) => {
          if ('triggerId' in payload) {
            await updateTrigger.mutateAsync({ triggerId: payload.triggerId, data: payload.data })
            setEditingTrigger(null)
          } else {
            await createTrigger.mutateAsync(payload)
            setShowForm(false)
          }
        }}
      />
    </div>
  )
}
