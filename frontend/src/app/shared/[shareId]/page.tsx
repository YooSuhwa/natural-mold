'use client'

import { use, useMemo } from 'react'
import Link from 'next/link'
import { AlertCircleIcon, ArrowLeftIcon, MessageSquareIcon } from 'lucide-react'

import { AgentAvatar } from '@/components/agent/agent-avatar'
import { MarkdownContent } from '@/components/chat/markdown-content'
import { CollapsiblePill } from '@/components/chat/tool-ui/collapsible-pill'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { usePublicShare } from '@/lib/hooks/use-share'
import { extractChips, type ChipInfo } from '@/lib/share/extract-chips'
import { formatLongDate, formatMediumDate } from '@/lib/utils/format-relative-time'
import type { Message } from '@/lib/types'
import type { SharedConversationView, TurnTrace } from '@/lib/types/share'

/** 공개 페이지에서 노출할 메시지 판정.
 *
 * 제외:
 * - tool role 전체 (도구 결과 메시지는 chips로 합쳐 표시)
 * - 본문이 빈 assistant (도구 호출만 담은 placeholder AIMessage. 라이브
 *   채팅 UI는 chips로 합쳐서 보여주지만 공개 페이지에선 별도 블록으로 나가
 *   "사내 위치 안내 도우미" 같은 헤더가 반복되는 시각 노이즈 발생)
 */
const isVisibleInPublic = (m: Message): boolean => {
  if (m.role === 'tool') return false
  if (m.role === 'assistant') {
    const content = typeof m.content === 'string' ? m.content : ''
    if (!content.trim()) return false
  }
  return true
}

interface PageProps {
  params: Promise<{ shareId: string }>
}

export default function SharedConversationPage({ params }: PageProps) {
  const { shareId } = use(params)
  const { data, isLoading, isError } = usePublicShare(shareId)

  if (isLoading) return <SharedSkeleton />
  if (isError || !data) return <SharedError />
  return <SharedArticle data={data} />
}

function SharedArticle({ data }: { data: SharedConversationView }) {
  const visibleMessages = useMemo(
    () => data.messages.filter(isVisibleInPublic),
    [data.messages],
  )

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <SharedHeader />

      <main className="flex-1">
        <article className="mx-auto w-full max-w-3xl px-5 sm:px-6">
          <Hero data={data} messageCount={visibleMessages.length} />

          {visibleMessages.length === 0 ? (
            <EmptyConversation />
          ) : (
            <ConversationBody
              messages={visibleMessages}
              agent={data.agent}
              traces={data.traces}
            />
          )}
        </article>
      </main>

      <SharedFooter messageCount={visibleMessages.length} createdAt={data.conversation_created_at} />
    </div>
  )
}

function SharedHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-5 sm:px-6">
        <Link
          href="/"
          className="flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground hover:text-primary-strong"
        >
          <span className="flex size-7 items-center justify-center rounded-lg bg-primary-strong/15 text-primary-strong">
            <span aria-hidden className="text-base">M</span>
          </span>
          Moldy
        </Link>
        <Link
          href="/"
          className="text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          나만의 에이전트 만들기 →
        </Link>
      </div>
    </header>
  )
}

function Hero({
  data,
  messageCount,
}: {
  data: SharedConversationView
  messageCount: number
}) {
  const readingMinutes = useReadingMinutes(data.messages)
  const dateLabel = useMemo(
    () => formatLongDate(data.conversation_created_at),
    [data.conversation_created_at],
  )

  return (
    <section className="pt-16 pb-2 text-center sm:pt-24">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        공유된 대화
      </p>
      <h1 className="mt-5 text-3xl font-light leading-tight tracking-tight text-foreground sm:text-4xl">
        {data.conversation_title ?? '제목 없는 대화'}
      </h1>

      <div className="mt-10 flex items-center justify-center gap-3">
        <AgentAvatar
          imageUrl={data.agent.image_url}
          name={data.agent.name}
          size="md"
        />
        <div className="text-left">
          <p className="text-sm font-semibold text-foreground">
            {data.agent.name}
          </p>
          <p className="text-[11px] tracking-wide text-muted-foreground">
            {dateLabel}
          </p>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
        <Badge variant="secondary">
          <MessageSquareIcon />
          {messageCount}개 메시지
        </Badge>
        {data.agent.description ? (
          <Badge
            variant="secondary"
            className="max-w-[260px] truncate"
            title={data.agent.description}
          >
            {data.agent.description}
          </Badge>
        ) : null}
        <Badge variant="secondary">
          {readingMinutes < 1 ? '1분 이내' : `약 ${readingMinutes}분 읽기`}
        </Badge>
      </div>
    </section>
  )
}

function ConversationBody({
  messages,
  agent,
  traces,
}: {
  messages: Message[]
  agent: SharedConversationView['agent']
  traces: TurnTrace[]
}) {
  // turn별 chips를 메시지에 미리 매핑 — trace의 assistant_msg_id는 stream
  // 시작 시 발급된 UUID4라 langchain message id (MessageResponse.id)와 다르
  // 므로 직접 매칭 불가능. 대신 conversation의 turn 경계(user → 첫 assistant)
  // 를 따라 traces[]를 시간순으로 1:1 매핑한다.
  const chipsByMessageId = useMemo(
    () => mapTurnChipsToMessages(messages, traces),
    [messages, traces],
  )

  return (
    <section className="py-10 sm:py-14">
      <DividerLabel>대화 기록</DividerLabel>
      <ol className="mt-10 flex flex-col gap-8">
        {messages.map((message) => (
          <SharedMessage
            key={message.id}
            message={message}
            agent={agent}
            chips={chipsByMessageId.get(message.id) ?? []}
          />
        ))}
      </ol>
    </section>
  )
}

/**
 * conversation의 user→assistant turn 경계마다 trace 1건씩 chronological
 * 매칭해서 "이 turn의 첫 assistant 메시지에 표시할 chips" 매핑을 만든다.
 *
 * branch가 있는 conversation(edit/regenerate)은 trace가 더 많을 수 있는데,
 * 공개 페이지는 active 브랜치만 노출하므로 trace 일부가 매핑되지 않을 수
 * 있다. 그 경우는 자연스럽게 칩이 안 보임 (graceful).
 */
function mapTurnChipsToMessages(
  messages: Message[],
  traces: TurnTrace[],
): Map<string, ChipInfo[]> {
  const map = new Map<string, ChipInfo[]>()
  let turnIdx = 0
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i]
    const prev = messages[i - 1]
    if (m.role === 'assistant' && prev?.role === 'user') {
      const trace = traces[turnIdx]
      turnIdx += 1
      if (trace) {
        const chips = extractChips(trace)
        if (chips.length > 0) map.set(m.id, chips)
      }
    }
  }
  return map
}

function DividerLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="h-px flex-1 bg-gradient-to-r from-transparent via-border to-transparent" />
      <span className="select-none text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {children}
      </span>
      <span className="h-px flex-1 bg-gradient-to-r from-transparent via-border to-transparent" />
    </div>
  )
}

function SharedMessage({
  message,
  agent,
  chips,
}: {
  message: Message
  agent: SharedConversationView['agent']
  chips: ChipInfo[]
}) {
  if (message.role === 'user') {
    return (
      <li className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl bg-muted/60 px-4 py-3 text-sm text-foreground">
          <p className="whitespace-pre-wrap break-words leading-relaxed">
            {message.content}
          </p>
        </div>
      </li>
    )
  }

  return (
    <li className="space-y-3">
      <div className="flex items-center gap-2">
        <AgentAvatar imageUrl={agent.image_url} name={agent.name} size="xs" />
        <span className="text-sm font-semibold text-foreground">
          {agent.name}
        </span>
      </div>
      <div className="pl-8 space-y-3">
        {chips.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {chips.map((chip, i) => (
              <CollapsiblePill
                key={i}
                kind={chip.kind}
                status={chip.status}
                title={chip.title}
                meta={chip.meta}
              />
            ))}
          </div>
        )}
        <MarkdownContent content={message.content} />
      </div>
    </li>
  )
}

function EmptyConversation() {
  return (
    <div className="py-16 text-center">
      <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-2xl bg-muted">
        <MessageSquareIcon className="size-5 text-muted-foreground" />
      </div>
      <p className="text-sm text-muted-foreground">
        아직 메시지가 없는 대화입니다.
      </p>
    </div>
  )
}

function SharedFooter({
  messageCount,
  createdAt,
}: {
  messageCount: number
  createdAt: string
}) {
  const dateLabel = useMemo(() => formatMediumDate(createdAt), [createdAt])

  return (
    <footer className="mx-auto mt-10 w-full max-w-3xl px-5 pb-12 sm:px-6">
      <div className="rounded-2xl border bg-gradient-to-br from-muted/40 to-background p-6 sm:flex sm:items-center sm:justify-between sm:gap-6 sm:p-8">
        <div className="text-center sm:text-left">
          <p className="text-base font-semibold tracking-tight text-foreground">
            나만의 AI 에이전트를 만들어 보세요
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Moldy로 도구·스킬을 조합한 에이전트를 노코드로 구축하고 공유하세요.
          </p>
        </div>
        <Link
          href="/"
          className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-primary-strong px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-opacity hover:opacity-90 sm:mt-0 sm:w-auto"
        >
          Moldy 시작하기 →
        </Link>
      </div>

      <div className="mt-6 flex flex-col items-center gap-2 text-[11px] text-muted-foreground sm:flex-row sm:justify-between">
        <div className="flex items-center gap-2">
          <span>{dateLabel}</span>
          <span className="size-0.5 rounded-full bg-border" />
          <span>{messageCount}개 메시지</span>
        </div>
        <span>Moldy로 만든 대화</span>
      </div>
    </footer>
  )
}

function SharedSkeleton() {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <SharedHeader />
      <main className="mx-auto w-full max-w-3xl px-5 pt-16 sm:px-6 sm:pt-24">
        <div className="space-y-6 text-center">
          <Skeleton className="mx-auto h-3 w-24" />
          <Skeleton className="mx-auto h-10 w-2/3" />
          <div className="mx-auto flex w-fit items-center gap-3">
            <Skeleton className="size-10 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-3 w-32" />
            </div>
          </div>
          <div className="mx-auto flex flex-wrap justify-center gap-2">
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-5 w-24 rounded-full" />
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
        </div>
        <div className="mt-14 space-y-8">
          <Skeleton className="ml-auto h-16 w-3/4 rounded-2xl" />
          <Skeleton className="h-24 w-3/4 rounded-2xl" />
        </div>
      </main>
    </div>
  )
}

function SharedError() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-6 text-center">
      <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-destructive/10">
        <AlertCircleIcon className="size-6 text-destructive" />
      </div>
      <h1 className="text-lg font-semibold tracking-tight text-foreground">
        공유된 대화를 찾을 수 없어요
      </h1>
      <p className="mt-2 max-w-sm text-sm text-muted-foreground">
        링크가 만료됐거나 공유가 해제된 것 같아요. 작성자에게 새 링크를
        요청해 주세요.
      </p>
      <Link
        href="/"
        className="mt-6 inline-flex items-center gap-2 rounded-xl bg-primary-strong px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-opacity hover:opacity-90"
      >
        <ArrowLeftIcon className="size-4" />
        홈으로 돌아가기
      </Link>
    </div>
  )
}

/**
 * Naive 200 wpm estimate. Tool calls / non-string content count as 0 to
 * avoid wild over-estimates from serialized payloads.
 */
function useReadingMinutes(messages: Message[]): number {
  return useMemo(() => {
    const totalWords = messages.reduce((acc, m) => {
      if (typeof m.content !== 'string') return acc
      return acc + m.content.split(/\s+/).filter(Boolean).length
    }, 0)
    return Math.max(1, Math.ceil(totalWords / 200))
  }, [messages])
}
