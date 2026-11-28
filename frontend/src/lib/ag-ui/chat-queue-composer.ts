'use client'

import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import {
  useAui,
  type AppendMessage,
  type Attachment,
  type CompleteAttachment,
  type ComposerRuntimeState,
} from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { reportClientWarning } from '@/lib/logging/client-logger'
import type { AgUiChatActions } from './chat-action-context'

const isComplete = (attachment: Attachment): attachment is CompleteAttachment =>
  attachment.status.type === 'complete'

type Draft = Pick<ComposerRuntimeState, 'text' | 'quote' | 'runConfig' | 'attachments'>

function sameDraft(left: Draft, right: Draft) {
  return (
    left.text === right.text &&
    left.quote === right.quote &&
    left.runConfig === right.runConfig &&
    left.attachments.length === right.attachments.length &&
    left.attachments.every((attachment, index) => attachment.id === right.attachments[index].id)
  )
}

/** Keep a server-bound draft in the composer until the server confirms receipt. */
export function useAgUiQueueComposer(
  actions: AgUiChatActions | null,
  conversationId: string | null,
  clearResourceContext?: () => void,
) {
  const aui = useAui()
  const t = useTranslations('chat.queue')
  const active = useRef<AbortController | null>(null)
  const retry = useRef<{ draft: Draft; message: AppendMessage; steer: boolean } | null>(null)
  const [sending, setSending] = useState<{ conversationId: string | null } | null>(null)

  useLayoutEffect(
    () => () => {
      active.current?.abort()
      active.current = null
      retry.current = null
      setSending(null)
    },
    [aui, conversationId],
  )

  const sendQueued = useCallback(
    async (steer: boolean) => {
      if (!actions || active.current) return
      const composer = aui.composer
      const draft = composer.getState()
      const thread = aui.thread.getState()
      if (draft.isEmpty || draft.canSend === false || thread.isDisabled) return
      const controller = new AbortController()
      active.current = controller
      setSending({ conversationId })
      try {
        let submission = retry.current
        if (!submission || steer !== submission.steer || !sameDraft(draft, submission.draft)) {
          const attachments = await Promise.all(
            draft.attachments.map(async (attachment) => {
              if (isComplete(attachment)) return attachment
              if (!actions.attachmentAdapter || attachment.status.type !== 'requires-action') {
                throw new Error('Attachment is not ready for upload')
              }
              return actions.attachmentAdapter.send(attachment, { signal: controller.signal })
            }),
          )
          if (controller.signal.aborted) return
          const selected = new Set(composer.getState().attachments.map((item) => item.id))
          const retained = attachments.filter((_, index) =>
            selected.has(draft.attachments[index].id),
          )
          if (!draft.text.trim() && !retained.length) return
          const message: AppendMessage = {
            role: 'user',
            parentId: thread.messages.at(-1)?.id ?? null,
            sourceId: null,
            createdAt: new Date(),
            content: draft.text ? [{ type: 'text', text: draft.text }] : [],
            attachments: retained,
            runConfig: draft.runConfig,
            metadata: { custom: draft.quote ? { quote: draft.quote } : {} },
          }
          submission = { draft, message, steer }
          retry.current = submission
        }
        await actions.queueDraft(submission.message, steer)
        if (controller.signal.aborted) return
        retry.current = null
        const current = composer.getState()
        if (!sameDraft(current, draft)) return
        clearResourceContext?.()
        composer.setText('', { origin: 'send' })
        composer.setQuote(undefined)
        await composer.clearAttachments()
      } catch (error) {
        if (!controller.signal.aborted) {
          reportClientWarning('AgUiQueueComposer', 'Could not confirm the queued message', error)
          toast.error(t('failed'))
        }
      } finally {
        if (active.current === controller) {
          active.current = null
          setSending(null)
        }
      }
    },
    [actions, aui, clearResourceContext, conversationId, t],
  )

  return { sendQueued, isSubmitting: sending !== null && sending.conversationId === conversationId }
}
