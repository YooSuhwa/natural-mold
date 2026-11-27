import type { ServerMessageQueueController } from '@/lib/chat/message-queue/server-message-queue-contract'

/** A lost POST reply can be accepted later by the controller's read-only queue poll. */
export function waitForChatQueueReceipt(
  queue: ServerMessageQueueController,
  requestId: string,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const check = () => {
      const { lastOperation, items } = queue.getSnapshot()
      const ours = 'requestId' in lastOperation && lastOperation.requestId === requestId
      if (!signal.aborted && ours && lastOperation.kind === 'reconciling') return
      unsubscribe()
      signal.removeEventListener('abort', check)
      if (signal.aborted) reject(signal.reason)
      else if (
        (ours && (lastOperation.kind === 'queued' || lastOperation.kind === 'applied')) ||
        items.some((item) => item.client_request_id === requestId)
      )
        resolve()
      else reject(new Error('The chat input has not been confirmed.'))
    }
    const unsubscribe = queue.subscribe(check)
    signal.addEventListener('abort', check, { once: true })
    check()
  })
}
