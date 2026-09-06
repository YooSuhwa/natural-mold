'use client'

import { useState } from 'react'
import { QueueItemPrimitive, type AppendMessage } from '@assistant-ui/react'
import { ArrowDownIcon, ArrowUpIcon, PencilIcon, PlayIcon, Trash2Icon, XIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useServerMessageQueueController } from '@/lib/chat/message-queue/server-message-queue-context'
import type { MessageQueueLabels } from './server-message-queue-panel'

function editedMessage(text: string): AppendMessage {
  return {
    role: 'user',
    content: [{ type: 'text', text }],
    attachments: [],
    createdAt: new Date(),
    parentId: null,
    sourceId: null,
    runConfig: {},
    metadata: { custom: {} },
  }
}

export function ServerMessageQueueItem({
  id,
  text,
  labels,
  previousId,
  nextId,
  officialActions,
}: {
  readonly id: string
  readonly text: string
  readonly labels: MessageQueueLabels
  readonly previousId?: string
  readonly nextId?: string
  readonly officialActions: boolean
}) {
  const controller = useServerMessageQueueController()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(text)
  const save = async () => {
    const trimmed = draft.trim()
    if (!trimmed) return
    await controller.edit(id, editedMessage(trimmed))
    setEditing(false)
  }

  return (
    <li className="moldy-muted-panel flex items-start gap-2 px-2.5 py-2" data-moldy-queue-item={id}>
      <div className="min-w-0 flex-1">
        {editing ? (
          <textarea
            value={draft}
            aria-label={labels.edit}
            className="min-h-10 w-full resize-y rounded-md border border-input bg-background px-2 py-1.5 text-sm outline-hidden"
            onChange={(event) => setDraft(event.currentTarget.value)}
          />
        ) : officialActions ? (
          <QueueItemPrimitive.Text className="line-clamp-2 text-sm text-foreground" />
        ) : (
          <span className="line-clamp-2 text-sm text-foreground">{text}</span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {editing ? (
          <>
            <Button type="button" size="sm" onClick={() => void save()}>
              {labels.save}
            </Button>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label={labels.cancelEdit}
              onClick={() => {
                setDraft(text)
                setEditing(false)
              }}
            >
              <XIcon className="size-3.5" />
            </Button>
          </>
        ) : (
          <>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label={labels.edit}
              data-moldy-queue-edit={id}
              onClick={() => setEditing(true)}
            >
              <PencilIcon className="size-3.5" />
            </Button>
            {officialActions ? (
              <QueueItemPrimitive.Steer asChild>
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label={labels.steer}
                  data-moldy-queue-steer={id}
                >
                  <PlayIcon className="size-3.5" />
                </Button>
              </QueueItemPrimitive.Steer>
            ) : null}
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              disabled={!previousId}
              aria-label={labels.moveUp}
              data-moldy-queue-move-up={id}
              onClick={() =>
                previousId && void controller.move(id, { lane: 'queue', insertBefore: previousId })
              }
            >
              <ArrowUpIcon className="size-3.5" />
            </Button>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              disabled={!nextId}
              aria-label={labels.moveDown}
              data-moldy-queue-move-down={id}
              onClick={() =>
                nextId && void controller.move(id, { lane: 'queue', insertAfter: nextId })
              }
            >
              <ArrowDownIcon className="size-3.5" />
            </Button>
            {officialActions ? (
              <QueueItemPrimitive.Remove asChild>
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label={labels.remove}
                  data-moldy-queue-remove={id}
                >
                  <Trash2Icon className="size-3.5" />
                </Button>
              </QueueItemPrimitive.Remove>
            ) : (
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                aria-label={labels.remove}
                data-moldy-queue-remove={id}
                onClick={() => void controller.remove(id)}
              >
                <Trash2Icon className="size-3.5" />
              </Button>
            )}
          </>
        )}
      </div>
    </li>
  )
}
