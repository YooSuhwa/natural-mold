import { useState } from 'react'
import { Trash2Icon, PlayIcon, PauseIcon, PlusIcon } from 'lucide-react'
import { useTranslations, useFormatter } from 'next-intl'
import { useTriggers, useCreateTrigger, useUpdateTrigger } from '@/lib/hooks/use-triggers'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ScheduleForm } from '@/components/agent/visual-settings/dialogs/schedule-dialog'

interface TriggersTabProps {
  agentId: string
  onRequestDelete: (target: { id: string; interval: number }) => void
}

export function TriggersTab({ agentId, onRequestDelete }: TriggersTabProps) {
  const t = useTranslations('agent.settings')
  const format = useFormatter()
  const { data: triggers } = useTriggers(agentId)
  const createTrigger = useCreateTrigger(agentId)
  const updateTrigger = useUpdateTrigger(agentId)

  const [showForm, setShowForm] = useState(false)

  return (
    <div className="space-y-3">
      {triggers && triggers.length > 0 ? (
        <div className="space-y-2">
          {triggers.map((trigger) => (
            <Card key={trigger.id}>
              <CardContent className="flex items-center justify-between py-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <Badge variant={trigger.status === 'active' ? 'default' : 'secondary'}>
                      {trigger.status === 'active' ? t('trigger.active') : t('trigger.paused')}
                    </Badge>
                    {trigger.trigger_type === 'interval' ? (
                      <span className="text-sm">
                        {t('trigger.interval', {
                          minutes: trigger.schedule_config.interval_minutes ?? 10,
                        })}
                      </span>
                    ) : (
                      <span className="font-mono text-xs">
                        {trigger.schedule_config.cron_expression}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground truncate max-w-md">
                    &quot;{trigger.input_message}&quot;
                  </p>
                  <div className="flex gap-3 text-xs text-muted-foreground">
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
                    aria-label={t('trigger.delete')}
                    onClick={() =>
                      onRequestDelete({
                        id: trigger.id,
                        interval: trigger.schedule_config?.interval_minutes ?? 0,
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

      {showForm ? (
        <div className="rounded-lg border p-4">
          <ScheduleForm
            isPending={createTrigger.isPending}
            onCancel={() => setShowForm(false)}
            onSubmit={async (req) => {
              await createTrigger.mutateAsync(req)
              setShowForm(false)
            }}
          />
        </div>
      ) : (
        <Button variant="outline" size="sm" onClick={() => setShowForm(true)}>
          <PlusIcon className="size-4" />
          {t('trigger.addNew')}
        </Button>
      )}
    </div>
  )
}

