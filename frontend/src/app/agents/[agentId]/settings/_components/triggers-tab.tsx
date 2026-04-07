import { useState } from 'react'
import { Loader2Icon, Trash2Icon, PlayIcon, PauseIcon, PlusIcon } from 'lucide-react'
import { useTranslations, useFormatter } from 'next-intl'
import { useTriggers, useCreateTrigger, useUpdateTrigger } from '@/lib/hooks/use-triggers'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

interface TriggersTabProps {
  agentId: string
  onRequestDelete: (target: { id: string; interval: number }) => void
}

export function TriggersTab({ agentId, onRequestDelete }: TriggersTabProps) {
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')
  const format = useFormatter()
  const { data: triggers } = useTriggers(agentId)
  const createTrigger = useCreateTrigger(agentId)
  const updateTrigger = useUpdateTrigger(agentId)

  const [showForm, setShowForm] = useState(false)
  const [minutes, setMinutes] = useState('10')
  const [message, setMessage] = useState('')

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
                    <span className="text-sm">
                      {t('trigger.interval', {
                        minutes: trigger.schedule_config.interval_minutes ?? 10,
                      })}
                    </span>
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
        <div className="space-y-3 rounded-lg border p-4">
          <div className="space-y-2">
            <label className="text-xs font-medium">{t('trigger.intervalLabel')}</label>
            <Input
              type="number"
              min="1"
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              placeholder="10"
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs font-medium">{t('trigger.messageLabel')}</label>
            <Textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder={t('trigger.messagePlaceholder')}
              rows={2}
            />
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={createTrigger.isPending || !message.trim()}
              onClick={async () => {
                await createTrigger.mutateAsync({
                  trigger_type: 'interval',
                  schedule_config: { interval_minutes: Number(minutes) || 10 },
                  input_message: message.trim(),
                })
                setShowForm(false)
                setMessage('')
                setMinutes('10')
              }}
            >
              {createTrigger.isPending && <Loader2Icon className="mr-1 size-3 animate-spin" />}
              {t('trigger.addButton')}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setShowForm(false)}>
              {tc('cancel')}
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="outline" size="sm" onClick={() => setShowForm(true)}>
          <PlusIcon className="size-4" data-icon="inline-start" />
          {t('trigger.addNew')}
        </Button>
      )}
    </div>
  )
}
