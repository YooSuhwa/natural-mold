export interface LifecycleSubscription {
  unsubscribe(): Promise<void>
}

interface ThreadLifecycleSubscriber {
  subscribe(
    channel: 'lifecycle',
    options: { namespaces: string[][]; depth: number },
  ): Promise<LifecycleSubscription>
}

export interface ThreadLifecycleStream {
  getThread(): ThreadLifecycleSubscriber | undefined
}

export async function refreshThreadLifecycleStream(
  stream: ThreadLifecycleStream,
): Promise<void> {
  const thread = stream.getThread()
  if (!thread) return
  const subscription = await thread.subscribe('lifecycle', {
    namespaces: [[]],
    depth: 0,
  })
  await subscription.unsubscribe()
}
