'use client'

import { useRef, useState } from 'react'
import { useAtomValue } from 'jotai'
import { useLocale, useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { childTaskLabels } from '@/lib/chat/child-task-labels'
import { chatSubagentNamesAtom } from '@/lib/stores/chat-subagent-names'
import {
  useChildTask,
  useChildTasks,
  useChildTaskCommands,
  isActiveChildTask,
  isRetryableChildTaskError,
  type ChildInstructionRequest,
  type ChildTask,
} from '@/lib/hooks/use-child-tasks'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

interface ManagedChildProps {
  readonly conversationId: string
  readonly taskId: string
}

export function ManagedChildTask({ conversationId, taskId }: ManagedChildProps) {
  const t = useTranslations('chat.childTasks')
  const list = useChildTasks(conversationId)
  const detail = useChildTask(conversationId, taskId)
  const names = useAtomValue(chatSubagentNamesAtom)[conversationId]
  const error = detail.error ?? list.error
  if (error) {
    const retryable = isRetryableChildTaskError(error)
    return (
      <div className="space-y-2">
        <p role="status">{t('unavailable')}</p>
        {retryable && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              void detail.refetch()
              void list.refetch()
            }}
          >
            {t('retry')}
          </Button>
        )}
      </div>
    )
  }
  if (detail.isPending || list.isPending) return <p role="status">{t('loading')}</p>
  const task = detail.data
  if (!task) return <p role="status">{t('unavailable')}</p>
  const label =
    childTaskLabels([...list.tasks.filter((item) => item.id !== taskId), task], names).get(
      taskId,
    ) ?? task.role
  return (
    <TaskDetails
      key={task.id}
      conversationId={conversationId}
      task={task}
      label={label}
      canSend={list.enabled === true}
    />
  )
}

function TaskDetails({
  conversationId,
  task,
  label,
  canSend,
}: {
  conversationId: string
  task: ChildTask
  label: string
  canSend: boolean
}) {
  const t = useTranslations('chat.childTasks')
  const locale = useLocale()
  const { send, stop, wait } = useChildTaskCommands(conversationId, task.id)
  const [text, setText] = useState('')
  const submission = useRef<ChildInstructionRequest | null>(null)
  function submit(event: React.FormEvent) {
    event.preventDefault()
    const value = text.trim()
    if (!value || send.isPending) return
    // A lost acknowledgement can be retried with the same id until the draft changes.
    if (submission.current?.text !== value) {
      submission.current = { client_request_id: crypto.randomUUID(), text: value }
    }
    send.mutate(submission.current, {
      onSuccess: () => {
        submission.current = null
        setText('')
      },
    })
  }

  return (
    <section aria-label={label} className="space-y-3">
      <h3 className="text-sm font-semibold">{label}</h3>
      <p className="moldy-ui-caption text-muted-foreground" role="status">
        {t(`status.${task.status}`)} ·{' '}
        <time dateTime={task.created_at}>{formatDisplayDateTime(task.created_at, { locale })}</time>
      </p>
      {task.brief && (
        <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">
          {task.brief}
        </p>
      )}
      {task.result && <p className="whitespace-pre-wrap break-words text-sm">{task.result}</p>}
      {task.instructions.length > 0 && (
        <ul aria-label={t('instructions')} className="space-y-2">
          {task.instructions.map((item) => (
            <li className="moldy-muted-panel px-3 py-2" key={item.id}>
              <p className="whitespace-pre-wrap break-words text-sm">{item.text}</p>
              <span className="moldy-ui-caption text-muted-foreground">
                {t(`delivery.${item.status}`)}
                {item.applied_at && (
                  <>
                    {' '}
                    ·{' '}
                    <time dateTime={item.applied_at}>
                      {formatDisplayDateTime(item.applied_at, { locale })}
                    </time>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {isActiveChildTask(task) && (
        <>
          {canSend && (
            <form onSubmit={submit} className="space-y-2">
              <Textarea
                aria-label={t('input', { role: label })}
                placeholder={t('input', { role: label })}
                maxLength={8000}
                value={text}
                disabled={send.isPending}
                onChange={(event) => setText(event.target.value)}
              />
              <Button
                type="submit"
                size="sm"
                disabled={!text.trim() || send.isPending || stop.isPending}
              >
                {t('send')}
              </Button>
            </form>
          )}
          {send.isError && (
            <p role="alert" className="text-sm text-status-danger">
              {t('sendError')}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={wait.isPending || stop.isPending}
              onClick={() => wait.mutate()}
            >
              {t(wait.isPending ? 'waiting' : 'wait')}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={stop.isPending || send.isPending}
              onClick={() => stop.mutate()}
            >
              {t('cancel')}
            </Button>
          </div>
          {wait.isSuccess && isActiveChildTask(wait.data) && (
            <p role="status" className="text-sm text-muted-foreground">
              {t('stillActive')}
            </p>
          )}
          {(wait.isError || stop.isError) && (
            <p role="alert" className="text-sm text-status-danger">
              {t('actionError')}
            </p>
          )}
        </>
      )}
    </section>
  )
}
