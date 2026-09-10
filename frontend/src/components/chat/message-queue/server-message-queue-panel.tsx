'use client'

import { ComposerPrimitive, useAui } from '@assistant-ui/react'
import { RotateCcwIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { queueItemText } from '@/lib/chat/message-queue/queue-message-projection'
import {
  useServerMessageQueueController,
  useServerMessageQueueSnapshot,
} from '@/lib/chat/message-queue/server-message-queue-context'
import { ServerMessageQueueItem } from './server-message-queue-item'

export type MessageQueueLabels = {
  readonly title: string
  readonly paused: string
  readonly resume: string
  readonly edit: string
  readonly save: string
  readonly cancelEdit: string
  readonly remove: string
  readonly steer: string
  readonly steerAction: string
  readonly sendNow: string
  readonly cancelSteer: string
  readonly moreActions: string
  readonly moveUp: string
  readonly moveDown: string
  readonly sending: string
  readonly queued: string
  readonly applied: string
  readonly failed: string
  readonly restore: string
}

export function ServerMessageQueuePanel({ labels }: { readonly labels: MessageQueueLabels }) {
  const controller = useServerMessageQueueController()
  const snapshot = useServerMessageQueueSnapshot()
  const aui = useAui()
  const ordinary = snapshot.items.filter((item) => item.status === 'pending' && item.priority < 100)
  const promoted = snapshot.items.filter(
    (item) => item.status === 'pending' && item.priority >= 100,
  )
  const rejected = snapshot.rejectedSubmission
  const restoreRejected = async (): Promise<void> => {
    if (!rejected) return
    const currentComposer = aui.composer.getState()
    const currentText = currentComposer.text
    const rejectedText = rejected.message.content
      .filter(
        (part): part is Extract<(typeof rejected.message.content)[number], { type: 'text' }> =>
          part.type === 'text',
      )
      .map((part) => part.text)
      .join('\n')
    aui.composer.setText(
      rejectedText && currentText ? `${rejectedText}\n${currentText}` : rejectedText || currentText,
    )
    const rejectedRunConfig = rejected.message.runConfig
    aui.composer.setRunConfig({
      ...rejectedRunConfig,
      ...currentComposer.runConfig,
      custom: {
        ...rejectedRunConfig?.custom,
        ...currentComposer.runConfig.custom,
      },
    })
    await Promise.all(
      (rejected.message.attachments ?? []).map((attachment) =>
        aui.composer.addAttachment({
          id: attachment.id,
          type: attachment.type,
          name: attachment.name,
          contentType: attachment.contentType,
          content: attachment.content,
        }),
      ),
    )
    controller.dismissRejectedSubmission()
  }
  if (
    ordinary.length === 0 &&
    promoted.length === 0 &&
    !snapshot.queuePaused &&
    snapshot.lastOperation.kind === 'idle'
  ) {
    return null
  }

  const operationLabel =
    snapshot.lastOperation.kind === 'sending'
      ? labels.sending
      : snapshot.lastOperation.kind === 'reconciling'
        ? labels.sending
        : snapshot.lastOperation.kind === 'queued'
          ? labels.queued
          : snapshot.lastOperation.kind === 'applied'
            ? labels.applied
            : snapshot.lastOperation.kind === 'failed'
              ? labels.failed
              : null

  return (
    <section className="border-b border-border/60 px-2.5 py-2" data-moldy-message-queue>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <p className="text-xs font-medium text-foreground/80">{labels.title}</p>
          {operationLabel ? (
            <span className="truncate text-xs text-muted-foreground" aria-live="polite">
              {operationLabel}
            </span>
          ) : null}
        </div>
        {snapshot.queuePaused ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-moldy-queue-resume
            onClick={() => void controller.resume()}
          >
            <RotateCcwIcon className="size-3.5" />
            {labels.resume}
          </Button>
        ) : null}
      </div>
      {snapshot.queuePaused ? (
        <p className="mb-1.5 text-xs text-muted-foreground">{labels.paused}</p>
      ) : null}
      <ul className="space-y-1.5">
        {rejected ? (
          <li className="flex items-center justify-between gap-2 rounded-lg border border-destructive/30 px-2 py-1.5">
            <span className="truncate text-xs text-muted-foreground">
              {rejected.message.content
                .filter((part) => part.type === 'text')
                .map((part) => part.text)
                .join('\n')}
            </span>
            {snapshot.lastOperation.kind === 'failed' ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void restoreRejected()}
              >
                {labels.restore}
              </Button>
            ) : null}
          </li>
        ) : null}
        <ComposerPrimitive.Queue>
          {({ queueItem }) => {
            const index = ordinary.findIndex((item) => item.id === queueItem.id)
            const isOrdinary = index >= 0
            return (
              <ServerMessageQueueItem
                id={queueItem.id}
                text={queueItemText(queueItem)}
                labels={labels}
                previousId={isOrdinary ? ordinary[index - 1]?.id : undefined}
                nextId={isOrdinary ? ordinary[index + 1]?.id : undefined}
                officialActions={isOrdinary}
              />
            )
          }}
        </ComposerPrimitive.Queue>
      </ul>
    </section>
  )
}
