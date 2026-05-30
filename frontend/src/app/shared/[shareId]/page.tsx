'use client'

import { use, useMemo } from 'react'
import Link from 'next/link'
import { AlertCircleIcon, ArrowLeftIcon, MessageSquareIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

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
  const t = useTranslations('sharedConversation')
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
          {t('brand')}
        </Link>
        <Link
          href="/"
          className="text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          {t('headerCta')}
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
  const t = useTranslations('sharedConversation')
  const readingMinutes = useReadingMinutes(data.messages)
  const dateLabel = useMemo(
    () => formatLongDate(data.conversation_created_at),
    [data.conversation_created_at],
  )

  return (
    <section className="pt-16 pb-2 text-center sm:pt-24">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {t('eyebrow')}
      </p>
      <h1 className="mt-5 text-3xl font-light leading-tight tracking-tight text-foreground sm:text-4xl">
        {data.conversation_title ?? t('titleFallback')}
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
          {t('footer.messageCount', { count: messageCount })}
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
          {readingMinutes < 1
            ? t('readingTime.underOneMinute')
            : t('readingTime.minutes', { minutes: readingMinutes })}
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
  const t = useTranslations('sharedConversation')
  // 라이브 채팅 UX와 동일하게: 연속된 assistant 메시지는 한 그룹으로 묶고
  // 그룹의 첫 메시지에 chips를 붙인다 ("도우미"가 같은 turn에서 여러 번
  // 말하더라도 헤더는 한 번만, 도구 칩도 그 위에 한 번만).
  const turnGroups = useMemo(
    () => groupMessagesIntoTurns(messages, traces),
    [messages, traces],
  )

  return (
    <section className="py-10 sm:py-14">
      <DividerLabel>{t('history')}</DividerLabel>
      <ol className="mt-10 flex flex-col gap-8">
        {turnGroups.map((group, i) =>
          group.kind === 'user' ? (
            <UserMessageItem key={group.message.id} message={group.message} />
          ) : (
            <AssistantTurnItem
              key={group.messages[0]?.id ?? `turn-${i}`}
              messages={group.messages}
              chips={group.chips}
              agent={agent}
            />
          ),
        )}
      </ol>
    </section>
  )
}

/** 한 turn = (user message) | (assistant 메시지 그룹 + 그 turn의 chips). */
type TurnGroup =
  | { kind: 'user'; message: Message }
  | { kind: 'assistant'; messages: Message[]; chips: ChipInfo[] }

/**
 * 메시지를 user / assistant-group으로 평탄화하면서 trace를 1:1 매핑.
 *
 * 매칭 우선순위:
 *  1. ``trace.linked_message_ids``에 그룹의 첫 message.id 포함 → 직접 매칭 (W6 정확도, m33+)
 *  2. 폴백: chronological turn 순서 (linked_message_ids가 NULL인 m32 이전 row)
 *
 * branch가 있는 대화는 active 외 trace가 매핑되지 않을 수 있다 (graceful).
 */
function groupMessagesIntoTurns(
  messages: Message[],
  traces: TurnTrace[],
): TurnGroup[] {
  const groups: TurnGroup[] = []
  // 직접 매칭용 인덱스 — assistant_msg_id가 아니라 linked_message_ids 펼침.
  const traceByMsgId = new Map<string, TurnTrace>()
  for (const t of traces) {
    if (!t.linked_message_ids) continue
    for (const id of t.linked_message_ids) traceByMsgId.set(id, t)
  }
  // 직접 매칭에 쓰인 trace는 폴백 큐에서 제외.
  const usedTraces = new Set<TurnTrace>()
  let turnIdx = 0

  for (let i = 0; i < messages.length; i++) {
    const m = messages[i]
    if (m.role === 'user') {
      groups.push({ kind: 'user', message: m })
      continue
    }
    if (m.role !== 'assistant') continue

    const lastGroup = groups[groups.length - 1]
    if (lastGroup && lastGroup.kind === 'assistant') {
      lastGroup.messages.push(m)
      continue
    }

    // 우선 직접 매칭 시도
    let trace = traceByMsgId.get(m.id)
    if (trace) {
      usedTraces.add(trace)
    } else {
      // 폴백: 직접 매칭에 쓰이지 않은 trace 중에서 chronological 다음
      while (turnIdx < traces.length && usedTraces.has(traces[turnIdx])) {
        turnIdx += 1
      }
      trace = traces[turnIdx]
      if (trace) {
        usedTraces.add(trace)
        turnIdx += 1
      }
    }
    const chips = trace ? extractChips(trace) : []
    groups.push({ kind: 'assistant', messages: [m], chips })
  }
  return groups
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

function UserMessageItem({ message }: { message: Message }) {
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

function AssistantTurnItem({
  messages,
  chips,
  agent,
}: {
  messages: Message[]
  chips: ChipInfo[]
  agent: SharedConversationView['agent']
}) {
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
        {messages.map((m) => (
          <MarkdownContent key={m.id} content={m.content} />
        ))}
      </div>
    </li>
  )
}

function EmptyConversation() {
  const t = useTranslations('sharedConversation')
  return (
    <div className="py-16 text-center">
      <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-2xl bg-muted">
        <MessageSquareIcon className="size-5 text-muted-foreground" />
      </div>
      <p className="text-sm text-muted-foreground">
        {t('empty')}
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
  const t = useTranslations('sharedConversation')
  const dateLabel = useMemo(() => formatMediumDate(createdAt), [createdAt])

  return (
    <footer className="mx-auto mt-10 w-full max-w-3xl px-5 pb-12 sm:px-6">
      <div className="rounded-2xl border bg-gradient-to-br from-muted/40 to-background p-6 sm:flex sm:items-center sm:justify-between sm:gap-6 sm:p-8">
        <div className="text-center sm:text-left">
          <p className="text-base font-semibold tracking-tight text-foreground">
            {t('footer.title')}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t('footer.description')}
          </p>
        </div>
        <Link
          href="/"
          className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-primary-strong px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-opacity hover:opacity-90 sm:mt-0 sm:w-auto"
        >
          {t('footer.cta')}
        </Link>
      </div>

      <div className="mt-6 flex flex-col items-center gap-2 text-[11px] text-muted-foreground sm:flex-row sm:justify-between">
        <div className="flex items-center gap-2">
          <span>{dateLabel}</span>
          <span className="size-0.5 rounded-full bg-border" />
          <span>{t('footer.messageCount', { count: messageCount })}</span>
        </div>
        <span>{t('footer.madeWith')}</span>
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
  const t = useTranslations('sharedConversation')
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-6 text-center">
      <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-destructive/10">
        <AlertCircleIcon className="size-6 text-destructive" />
      </div>
      <h1 className="text-lg font-semibold tracking-tight text-foreground">
        {t('error.title')}
      </h1>
      <p className="mt-2 max-w-sm text-sm text-muted-foreground">
        {t('error.description')}
      </p>
      <Link
        href="/"
        className="mt-6 inline-flex items-center gap-2 rounded-xl bg-primary-strong px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-opacity hover:opacity-90"
      >
        <ArrowLeftIcon className="size-4" />
        {t('error.home')}
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
