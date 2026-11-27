import type { FeedbackAdapter } from '@assistant-ui/react'

import { reportClientError } from '@/lib/logging/client-logger'
import { storedFeedbackTarget } from './chat-message-identity'
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
        const id = storedFeedbackTarget(feedback.message, messageLinks)
        if (!id) {
          throw new Error('The selected message has no stored feedback target.')
        }
        original.submit({ ...feedback, message: { ...feedback.message, id } })
      } catch (error) {
        reportClientError('feedback', 'Could not resolve the stored feedback target.', error)
      }
    },
  }
}
