'use client'

import { useState } from 'react'
import { QueueItemPrimitive, type AppendMessage } from '@assistant-ui/react'
import {
  ArrowDownIcon,
  ArrowUpIcon,
  EllipsisIcon,
  FastForwardIcon,
  PencilIcon,
  Trash2Icon,
  XIcon,
} from 'lucide-react'

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
  const [steerArmed, setSteerArmed] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const [draft, setDraft] = useState(text)
  const save = async () => {
    const trimmed = draft.trim()
    if (!trimmed) return
    await controller.edit(id, editedMessage(trimmed))
    setEditing(false)
  }

  return (
    <li
      className="moldy-muted-panel flex flex-wrap items-start gap-2 px-2.5 py-2"
      data-moldy-queue-item={id}
    >
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
      <div className="flex shrink-0 items-center gap-1">
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
            {officialActions && !steerArmed ? (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                aria-label={labels.steer}
                data-moldy-queue-steer={id}
                onClick={() => setSteerArmed(true)}
              >
                <FastForwardIcon className="size-3.5" />
                {labels.steerAction}
              </Button>
            ) : officialActions ? (
              <>
                <QueueItemPrimitive.Steer asChild>
                  <Button
                    type="button"
                    size="sm"
                    aria-label={labels.sendNow}
                    data-moldy-queue-send-now={id}
                  >
                    <FastForwardIcon className="size-3.5" />
                    {labels.sendNow}
                  </Button>
                </QueueItemPrimitive.Steer>
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label={labels.cancelSteer}
                  onClick={() => setSteerArmed(false)}
                >
                  <XIcon className="size-3.5" />
                </Button>
              </>
            ) : null}
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label={labels.moreActions}
              aria-expanded={moreOpen}
              onClick={() => setMoreOpen((value) => !value)}
            >
              <EllipsisIcon className="size-3.5" />
            </Button>
          </>
        )}
      </div>
      {!editing && moreOpen ? (
        <div
          role="group"
          aria-label={labels.moreActions}
          className="flex basis-full flex-wrap justify-end gap-1 border-t border-border/50 pt-1.5"
        >
          <Button
            type="button"
            size="sm"
            variant="ghost"
            data-moldy-queue-edit={id}
            onClick={() => {
              setMoreOpen(false)
              setEditing(true)
            }}
          >
            <PencilIcon />
            {labels.edit}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={!previousId}
            data-moldy-queue-move-up={id}
            onClick={() =>
              previousId && void controller.move(id, { lane: 'queue', insertBefore: previousId })
            }
          >
            <ArrowUpIcon />
            {labels.moveUp}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={!nextId}
            data-moldy-queue-move-down={id}
            onClick={() =>
              nextId && void controller.move(id, { lane: 'queue', insertAfter: nextId })
            }
          >
            <ArrowDownIcon />
            {labels.moveDown}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="text-destructive hover:text-destructive"
            data-moldy-queue-remove={id}
            onClick={() => void controller.remove(id)}
          >
            <Trash2Icon />
            {labels.remove}
          </Button>
        </div>
      ) : null}
    </li>
  )
}
