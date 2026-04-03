'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { PlusIcon, MessageSquareIcon } from 'lucide-react'
import { useTranslations, useFormatter } from 'next-intl'
import { useConversations, useCreateConversation } from '@/lib/hooks/use-conversations'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useRouter } from 'next/navigation'

interface ConversationListProps {
  agentId: string
}

export function ConversationList({ agentId }: ConversationListProps) {
  const params = useParams<{ conversationId: string }>()
  const router = useRouter()
  const { data: conversations, isLoading } = useConversations(agentId)
  const createConversation = useCreateConversation(agentId)
  const t = useTranslations('chat.conversationList')
  const format = useFormatter()

  async function handleNewConversation() {
    const conv = await createConversation.mutateAsync(undefined)
    router.push(`/agents/${agentId}/conversations/${conv.id}`)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b p-3">
        <h2 className="text-sm font-medium">{t('title')}</h2>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={handleNewConversation}
          disabled={createConversation.isPending}
        >
          <PlusIcon className="size-3.5" />
          <span className="sr-only">{t('newConversation')}</span>
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-1 p-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : conversations && conversations.length > 0 ? (
          <div className="space-y-0.5 p-2">
            {conversations.map((conv) => (
              <Link
                key={conv.id}
                href={`/agents/${agentId}/conversations/${conv.id}`}
                className={cn(
                  'flex items-center gap-2 rounded-lg px-3 py-2 text-sm cursor-pointer transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  params.conversationId === conv.id && 'bg-muted font-medium',
                )}
              >
                <MessageSquareIcon className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate">{conv.title ?? t('fallbackTitle')}</span>
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                  {format.dateTime(new Date(conv.updated_at), {
                    month: 'numeric',
                    day: 'numeric',
                  })}
                </span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="p-4 text-center text-xs text-muted-foreground">{t('empty')}</div>
        )}
      </div>
    </div>
  )
}
