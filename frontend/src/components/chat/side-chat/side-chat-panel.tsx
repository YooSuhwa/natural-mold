'use client'

import { useCallback, useSyncExternalStore } from 'react'
import { Provider as JotaiProvider } from 'jotai'
import { useQueryClient } from '@tanstack/react-query'
import { MessageSquarePlusIcon, LoaderCircleIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { ChatRuntimeSection } from '@/components/chat/chat-runtime-section'
import { ChatRightRail } from '@/components/chat/right-rail/chat-right-rail'
import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { conversationKeys, useMessagesEnvelope } from '@/lib/hooks/use-conversations'
import { streamChat } from '@/lib/sse/stream-chat'
import type { Agent, Message } from '@/lib/types'
import type { User } from '@/lib/types/user'
import { useSideChat } from './side-chat-context'
import { SideChatToolbar } from './side-chat-toolbar'
import styles from './side-chat.module.css'

const EMPTY_MESSAGES: Message[] = []
const noStatusChange = () => {}
const subscribe = (notify: () => void) => {
  const media = window.matchMedia('(min-width: 1280px)')
  media.addEventListener('change', notify)
  return () => media.removeEventListener('change', notify)
}
const desktopSnapshot = () => window.matchMedia('(min-width: 1280px)').matches
const serverSnapshot = () => false

export function SideChatPanel({
  agent,
  user,
}: {
  readonly agent?: Agent
  readonly user?: User | null
}) {
  const workspace = useSideChat()
  const t = useTranslations('chat.sideChat')
  const desktop = useSyncExternalStore(subscribe, desktopSnapshot, serverSnapshot)
  if (!workspace) return null
  const content = workspace.sideId ? (
    <JotaiProvider key={workspace.sideId}>
      <SideChatRun conversationId={workspace.sideId} agent={agent} user={user} />
    </JotaiProvider>
  ) : (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      {workspace.loading ? (
        <LoaderCircleIcon className="size-5 animate-spin motion-reduce:animate-none" />
      ) : null}
      <p className="text-sm text-muted-foreground">
        {t(workspace.error ? 'loadFailed' : 'loading')}
      </p>
      {workspace.error ? <Button onClick={workspace.show}>{t('retry')}</Button> : null}
      <Button variant="ghost" onClick={workspace.close}>
        {t('close')}
      </Button>
    </div>
  )
  if (desktop)
    return (
      <aside
        aria-label={t('title')}
        hidden={!workspace.open}
        className={`moldy-panel min-h-0 overflow-hidden bg-background ${styles.panel}`}
      >
        {workspace.sideId || workspace.open ? content : null}
      </aside>
    )
  return (
    <DialogShell
      open={workspace.open}
      onOpenChange={(open) => {
        if (!open) workspace.close()
      }}
      size="xl"
      height="fixed"
    >
      <DialogShell.Header title={t('title')} srOnly />
      <div className="min-h-0 flex-1">{content}</div>
    </DialogShell>
  )
}

function SideChatRun({
  conversationId,
  agent,
  user,
}: {
  readonly conversationId: string
  readonly agent?: Agent
  readonly user?: User | null
}) {
  const t = useTranslations('chat.sideChat')
  const queryClient = useQueryClient()
  const envelope = useMessagesEnvelope(conversationId)
  const onStreamEnd = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: conversationKeys.messages(conversationId) })
  }, [queryClient, conversationId])
  const stream = useCallback(
    (content: string, signal: AbortSignal) => streamChat(conversationId, content, signal),
    [conversationId],
  )
  return (
    <ChatRuntimeSection
      activeConversationId={conversationId}
      activeRun={envelope.data?.active_run ?? null}
      agentId={agent?.id ?? ''}
      agentName={agent?.name}
      agentImageUrl={agent?.image_url}
      latestRun={envelope.data?.latest_run ?? null}
      messages={envelope.data?.messages ?? EMPTY_MESSAGES}
      modelName={agent?.model?.display_name}
      useLangGraphRuntime
      compact
      user={user}
      onRuntimeStatusChange={noStatusChange}
      onStreamEnd={onStreamEnd}
      streamFn={stream}
      linkedSkills={agent?.skills}
      threadHeader={<SideChatToolbar agentId={agent?.id ?? ''} />}
      emptyContent={
        <div className="flex h-full min-h-56 flex-col items-center justify-center gap-3 px-6 py-12 text-center">
          <MessageSquarePlusIcon className="size-7 text-muted-foreground" />
          <h2 className="text-base font-medium">{t('title')}</h2>
          <p className="max-w-72 break-keep text-sm leading-relaxed text-muted-foreground">
            {t('emptyDescription')}
          </p>
          <p className="max-w-72 break-keep text-xs leading-relaxed text-muted-foreground">
            {t('storageNotice')}
          </p>
        </div>
      }
    />
  )
}

export function SideChatToggle() {
  const workspace = useSideChat()
  const t = useTranslations('chat.sideChat')
  if (!workspace?.mainId) return null
  return (
    <Button
      variant={workspace.open ? 'secondary' : 'ghost'}
      size="icon-sm"
      aria-label={t('title')}
      aria-expanded={workspace.open}
      onClick={() => (workspace.open ? workspace.close() : workspace.show())}
    >
      <MessageSquarePlusIcon className="size-4" />
    </Button>
  )
}

export function ConversationDetailRail({
  conversationId,
}: {
  readonly conversationId: string | null
}) {
  const workspace = useSideChat()
  if (workspace?.open) return null
  return <ChatRightRail conversationId={conversationId} className="moldy-panel overflow-hidden" />
}
