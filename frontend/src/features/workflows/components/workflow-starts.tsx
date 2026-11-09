'use client'

import { useState } from 'react'
import { CalendarClockIcon, KeyRoundIcon, Trash2Icon, WebhookIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { API_BASE, ApiError } from '@/lib/api/client'
import {
  useWorkflowScheduleMutations,
  useWorkflowSchedules,
  useWorkflowVersion,
  useWorkflowWebhookMutations,
  useWorkflowWebhooks,
} from '@/lib/hooks/use-workflows'
import type {
  WorkflowSchedule,
  WorkflowScheduleInput,
  WorkflowWebhookCreated,
} from '@/lib/types/workflow'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

import { RunInputFields, typedInputs, type InputValues } from './run-input-fields'

const SELECT_CLASS =
  'h-9 rounded-md border bg-background px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring'
type Kind = 'daily' | 'interval' | 'cron' | 'once'

function problem(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback
  const problems = error.details?.problems as { name: string; message: string }[] | undefined
  return problems?.length
    ? problems.map((item) => `${item.name}: ${item.message}`).join(' / ')
    : error.message
}

/** When a schedule fires, in words (the cron form is shown as written). */
function timing(
  schedule: WorkflowSchedule,
  t: (key: string, v?: Record<string, string>) => string,
) {
  if (schedule.kind === 'interval') {
    return t('every', { minutes: String(schedule.config.minutes ?? '') })
  }
  if (schedule.kind === 'once') return t('onceAt', { at: schedule.config.at ?? '' })
  const daily = /^(\d{1,2}) (\d{1,2}) \* \* \*$/.exec(schedule.config.expression ?? '')
  if (daily) {
    return t('dailyAt', { time: `${daily[2].padStart(2, '0')}:${daily[1].padStart(2, '0')}` })
  }
  return t('cronAt', { expression: schedule.config.expression ?? '' })
}

/**
 * 예약 (workflow-starts-sharing-plan 3.1): fixed inputs at a time or interval. The person who
 * makes a schedule is the runner of its runs; three refused starts in a row pause it.
 */
export function WorkflowSchedules({
  workflowId,
  versionNo,
}: {
  workflowId: string
  versionNo: number | null
}) {
  const t = useTranslations('workflows.starts')
  const schedules = useWorkflowSchedules(workflowId)
  const actions = useWorkflowScheduleMutations(workflowId)
  const [adding, setAdding] = useState(false)
  return (
    <section className="moldy-panel flex flex-col gap-3 p-4" aria-label={t('schedulesTitle')}>
      <div className="flex items-center gap-2">
        <CalendarClockIcon className="size-4" aria-hidden />
        <h2 className="text-sm font-semibold">{t('schedulesTitle')}</h2>
        <span className="flex-1" />
        <Button
          size="sm"
          variant="outline"
          disabled={versionNo === null}
          title={versionNo === null ? t('needsPublish') : undefined}
          onClick={() => setAdding((open) => !open)}
        >
          {t('addSchedule')}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t('schedulesHint')}</p>
      {adding && versionNo !== null ? (
        <ScheduleForm
          workflowId={workflowId}
          versionNo={versionNo}
          onDone={() => setAdding(false)}
        />
      ) : null}
      <ul className="flex flex-col divide-y" aria-label={t('scheduleList')}>
        {(schedules.data ?? []).map((schedule) => (
          <li key={schedule.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <span className="font-medium">{schedule.name}</span>
            <span className="text-muted-foreground">{timing(schedule, t)}</span>
            <span
              className={
                schedule.status === 'active'
                  ? 'moldy-status-surface moldy-status-success rounded-full px-2 py-0.5 text-xs'
                  : 'moldy-status-surface moldy-status-warn rounded-full px-2 py-0.5 text-xs'
              }
            >
              {t(`scheduleStatus.${schedule.status}`)}
            </span>
            {schedule.next_run_at ? (
              <span className="text-xs text-muted-foreground">
                {t('nextRun', { at: formatDisplayDateTime(schedule.next_run_at) })}
              </span>
            ) : null}
            {schedule.last_error ? (
              <span className="text-xs text-status-danger">{schedule.last_error}</span>
            ) : null}
            <span className="flex-1" />
            <Button
              size="sm"
              variant="outline"
              disabled={actions.setStatus.isPending}
              onClick={() =>
                actions.setStatus.mutate(
                  {
                    id: schedule.id,
                    status: schedule.status === 'active' ? 'paused' : 'active',
                  },
                  { onError: (error) => toast.error(problem(error, t('failed'))) },
                )
              }
            >
              {schedule.status === 'active' ? t('pause') : t('resume')}
            </Button>
            <Button
              size="icon"
              variant="ghost"
              aria-label={t('removeSchedule', { name: schedule.name })}
              disabled={actions.remove.isPending}
              onClick={() => actions.remove.mutate(schedule.id)}
            >
              <Trash2Icon className="size-4" aria-hidden />
            </Button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ScheduleForm({
  workflowId,
  versionNo,
  onDone,
}: {
  workflowId: string
  versionNo: number
  onDone: () => void
}) {
  const t = useTranslations('workflows.starts')
  const version = useWorkflowVersion(workflowId, versionNo)
  const { create } = useWorkflowScheduleMutations(workflowId)
  const [name, setName] = useState('')
  const [kind, setKind] = useState<Kind>('daily')
  const [time, setTime] = useState('09:00')
  const [minutes, setMinutes] = useState('60')
  const [expression, setExpression] = useState('0 9 * * 1-5')
  const [at, setAt] = useState('')
  const [values, setValues] = useState<InputValues>({})
  // File inputs cannot be given on a schedule (starts plan S4).
  const inputs = (version.data?.definition.inputs ?? []).filter((input) => input.type !== 'file')
  const build = (): WorkflowScheduleInput => {
    const base = {
      name: name.trim() || t('defaultName'),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Seoul',
      inputs: typedInputs(inputs, values),
    }
    if (kind === 'interval') return { ...base, kind, config: { minutes: Number(minutes) } }
    if (kind === 'once') return { ...base, kind, config: { at } }
    if (kind === 'cron') return { ...base, kind, config: { expression } }
    const [hour, minute] = time.split(':').map((part) => Number(part))
    return { ...base, kind: 'cron', config: { expression: `${minute ?? 0} ${hour ?? 9} * * *` } }
  }
  return (
    <form
      className="moldy-muted-panel flex flex-col gap-3 p-3"
      aria-label={t('newSchedule')}
      onSubmit={(event) => {
        event.preventDefault()
        create.mutate(build(), {
          onSuccess: () => {
            toast.success(t('created'))
            onDone()
          },
          onError: (error) => toast.error(problem(error, t('failed'))),
        })
      }}
    >
      <Input
        value={name}
        placeholder={t('defaultName')}
        aria-label={t('scheduleName')}
        maxLength={100}
        onChange={(event) => setName(event.target.value)}
      />
      <div className="flex flex-wrap items-center gap-2">
        <select
          className={SELECT_CLASS}
          value={kind}
          aria-label={t('kindLabel')}
          onChange={(event) => setKind(event.target.value as Kind)}
        >
          {(['daily', 'interval', 'cron', 'once'] as const).map((item) => (
            <option key={item} value={item}>
              {t(`kind.${item}`)}
            </option>
          ))}
        </select>
        {kind === 'daily' ? (
          <Input
            type="time"
            className="w-32"
            value={time}
            aria-label={t('time')}
            onChange={(event) => setTime(event.target.value)}
          />
        ) : null}
        {kind === 'interval' ? (
          <Input
            type="number"
            min={5}
            className="w-32"
            value={minutes}
            aria-label={t('minutes')}
            onChange={(event) => setMinutes(event.target.value)}
          />
        ) : null}
        {kind === 'cron' ? (
          <Input
            className="w-48 font-mono"
            value={expression}
            aria-label={t('expression')}
            onChange={(event) => setExpression(event.target.value)}
          />
        ) : null}
        {kind === 'once' ? (
          <Input
            type="datetime-local"
            className="w-56"
            value={at}
            aria-label={t('at')}
            onChange={(event) => setAt(event.target.value)}
          />
        ) : null}
      </div>
      <RunInputFields
        inputs={inputs}
        values={values}
        onChange={(field, value) => setValues((current) => ({ ...current, [field]: value }))}
      />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={create.isPending}>
          {t('save')}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onDone}>
          {t('cancel')}
        </Button>
      </div>
    </form>
  )
}

/**
 * 웹훅 (workflow-starts-sharing-plan 3.2): an address and a secret token an outside system
 * starts runs with. The token is shown once, right after it is made.
 */
export function WorkflowWebhooks({ workflowId }: { workflowId: string }) {
  const t = useTranslations('workflows.starts')
  const hooks = useWorkflowWebhooks(workflowId)
  const actions = useWorkflowWebhookMutations(workflowId)
  const [name, setName] = useState('')
  const [fresh, setFresh] = useState<WorkflowWebhookCreated | null>(null)
  const urlOf = (publicId: string) => `${API_BASE}/hooks/workflows/${publicId}/runs`
  const onMade = (made: WorkflowWebhookCreated) => setFresh(made)
  return (
    <section className="moldy-panel flex flex-col gap-3 p-4" aria-label={t('webhooksTitle')}>
      <div className="flex items-center gap-2">
        <WebhookIcon className="size-4" aria-hidden />
        <h2 className="text-sm font-semibold">{t('webhooksTitle')}</h2>
      </div>
      <p className="text-xs text-muted-foreground">{t('webhooksHint')}</p>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          actions.create.mutate(name.trim() || t('defaultHookName'), {
            onSuccess: (made) => {
              setName('')
              onMade(made)
            },
            onError: (error) => toast.error(problem(error, t('failed'))),
          })
        }}
      >
        <Input
          className="w-64"
          value={name}
          maxLength={100}
          placeholder={t('defaultHookName')}
          aria-label={t('hookName')}
          onChange={(event) => setName(event.target.value)}
        />
        <Button type="submit" size="sm" variant="outline" disabled={actions.create.isPending}>
          {t('addWebhook')}
        </Button>
      </form>
      {fresh ? (
        <div
          className="moldy-status-surface moldy-status-info flex flex-col gap-2 rounded-md p-3 text-xs"
          role="status"
        >
          <p className="font-medium">{t('tokenOnce', { name: fresh.name })}</p>
          <code className="break-all font-mono" data-testid="webhook-token">
            {fresh.token}
          </code>
          <pre className="moldy-muted-panel overflow-auto p-2 font-mono whitespace-pre-wrap">
            {`curl -X POST ${urlOf(fresh.public_id)} \\\n  -H "Authorization: Bearer ${fresh.token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"inputs": {}}'`}
          </pre>
          <Button size="sm" variant="outline" className="self-start" onClick={() => setFresh(null)}>
            {t('tokenSaved')}
          </Button>
        </div>
      ) : null}
      <ul className="flex flex-col divide-y" aria-label={t('webhookList')}>
        {(hooks.data ?? []).map((hook) => (
          <li key={hook.id} className="flex flex-col gap-1 py-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{hook.name}</span>
              <span
                className={
                  hook.status === 'active'
                    ? 'moldy-status-surface moldy-status-success rounded-full px-2 py-0.5 text-xs'
                    : 'moldy-status-surface moldy-status-warn rounded-full px-2 py-0.5 text-xs'
                }
              >
                {t(`hookStatus.${hook.status}`)}
              </span>
              <span className="text-xs text-muted-foreground">
                {t('tokenHint', { hint: hook.token_hint })}
              </span>
              <span className="flex-1" />
              <Button
                size="sm"
                variant="outline"
                disabled={actions.rotate.isPending}
                onClick={() =>
                  actions.rotate.mutate(hook.id, {
                    onSuccess: onMade,
                    onError: (error) => toast.error(problem(error, t('failed'))),
                  })
                }
              >
                <KeyRoundIcon className="size-4" aria-hidden />
                {t('rotate')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={actions.setStatus.isPending}
                onClick={() =>
                  actions.setStatus.mutate({
                    id: hook.id,
                    status: hook.status === 'active' ? 'disabled' : 'active',
                  })
                }
              >
                {hook.status === 'active' ? t('disable') : t('enable')}
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label={t('removeWebhook', { name: hook.name })}
                disabled={actions.remove.isPending}
                onClick={() => actions.remove.mutate(hook.id)}
              >
                <Trash2Icon className="size-4" aria-hidden />
              </Button>
            </div>
            <code className="break-all font-mono text-xs text-muted-foreground">
              {urlOf(hook.public_id)}
            </code>
          </li>
        ))}
      </ul>
    </section>
  )
}
