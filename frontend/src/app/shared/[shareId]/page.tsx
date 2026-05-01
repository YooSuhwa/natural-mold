'use client'

import { use } from 'react'
import Link from 'next/link'
import { GlobeIcon } from 'lucide-react'

import { AgentAvatar } from '@/components/agent/agent-avatar'
import { MarkdownContent } from '@/components/chat/markdown-content'
import { Skeleton } from '@/components/ui/skeleton'
import { usePublicShare } from '@/lib/hooks/use-share'
import { cn } from '@/lib/utils'
import type { Message } from '@/lib/types'

interface PageProps {
  params: Promise<{ shareId: string }>
}

export default function SharedConversationPage({ params }: PageProps) {
  const { shareId } = use(params)
  const { data, isLoading, isError } = usePublicShare(shareId)

  return (
    <main className="min-h-screen bg-background">
      <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-4 py-8">
        {isLoading ? (
          <SharedSkeleton />
        ) : isError || !data ? (
          <SharedError />
        ) : (
          <>
            <SharedHeader
              agentName={data.agent.name}
              agentImageUrl={data.agent.image_url}
              agentDescription={data.agent.description}
              conversationTitle={data.conversation_title}
            />
            <ol className="mt-8 flex flex-col gap-6">
              {data.messages.length === 0 ? (
                <li className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                  아직 메시지가 없는 대화입니다.
                </li>
              ) : (
                data.messages.map((message) => (
                  <SharedMessage key={message.id} message={message} />
                ))
              )}
            </ol>
            <SharedFooter />
          </>
        )}
      </div>
    </main>
  )
}

function SharedHeader({
  agentName,
  agentImageUrl,
  agentDescription,
  conversationTitle,
}: {
  agentName: string
  agentImageUrl: string | null
  agentDescription: string | null
  conversationTitle: string | null
}) {
  return (
    <header className="flex items-start gap-4 border-b pb-6">
      <AgentAvatar imageUrl={agentImageUrl} name={agentName} size="lg" />
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-xl font-semibold">
          {conversationTitle ?? '공유된 대화'}
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          <span className="font-medium text-foreground">{agentName}</span>
          {agentDescription ? <> · {agentDescription}</> : null}
        </p>
      </div>
    </header>
  )
}

function SharedMessage({ message }: { message: Message }) {
  // Skip non-renderable system / tool-result rows; the public view sticks to
  // the user/assistant exchange. Tool calls and tool results are an
  // implementation detail of the agent run.
  if (message.role !== 'user' && message.role !== 'assistant') return null
  const isUser = message.role === 'user'

  return (
    <li className={cn('flex w-full', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[85%] rounded-2xl px-4 py-3 text-sm',
          isUser
            ? 'bg-primary text-primary-foreground'
            : 'border bg-card text-card-foreground',
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        ) : (
          <MarkdownContent content={message.content} />
        )}
      </div>
    </li>
  )
}

function SharedSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start gap-4 border-b pb-6">
        <Skeleton className="size-12 rounded-full" />
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-64" />
        </div>
      </div>
      <Skeleton className="ml-auto h-16 w-3/4 rounded-2xl" />
      <Skeleton className="h-24 w-3/4 rounded-2xl" />
      <Skeleton className="ml-auto h-12 w-1/2 rounded-2xl" />
    </div>
  )
}

function SharedError() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <GlobeIcon className="size-10 text-muted-foreground" />
      <h1 className="text-lg font-semibold">공유된 대화를 찾을 수 없어요</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        링크가 만료됐거나 공유가 해제된 것 같아요. 작성자에게 새 링크를 요청해주세요.
      </p>
      <Link
        href="/"
        className="mt-2 text-sm font-medium text-primary-strong hover:underline"
      >
        홈으로 돌아가기
      </Link>
    </div>
  )
}

function SharedFooter() {
  return (
    <footer className="mt-12 flex items-center justify-center border-t pt-6 text-xs text-muted-foreground">
      <Link href="/" className="hover:text-foreground">
        Moldy로 만든 대화
      </Link>
    </footer>
  )
}
