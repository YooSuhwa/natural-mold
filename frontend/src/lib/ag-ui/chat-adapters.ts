import type { FeedbackAdapter } from '@assistant-ui/react'
import { z } from 'zod'

import { reportClientError } from '@/lib/logging/client-logger'
import { findChatMessageLink } from './chat-message-identity'
import type { WireChatSnapshot } from './chat-history-types'

export { agUiAttachmentAdapter } from './chat-attachment-adapter'

/** Feedback URLs use the stored public message UUID, even when AG-UI generated a local ID. */
export function agUiFeedbackAdapter(
  original: FeedbackAdapter | undefined,
  readSnapshot: () => Promise<Pick<WireChatSnapshot, 'messageLinks'>>,
): FeedbackAdapter | undefined {
  if (!original) return undefined
  return {
    submit: async (feedback) => {
      try {
        const { messageLinks } = await readSnapshot()
        const id = findChatMessageLink(feedback.message, messageLinks)?.uiMessageId
        if (!z.string().uuid().safeParse(id).success || !id) {
          throw new Error('The selected message has no stored feedback target.')
        }
        original.submit({ ...feedback, message: { ...feedback.message, id } })
      } catch (error) {
        reportClientError('feedback', 'Could not resolve the stored feedback target.', error)
      }
    },
  }
}
