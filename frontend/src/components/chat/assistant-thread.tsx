'use client'

import { useState } from 'react'
import {
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
  ActionBarPrimitive,
  useThreadViewport,
  useAssistantState,
  type AssistantToolUI,
} from '@assistant-ui/react'
import { StreamdownTextPrimitive } from '@assistant-ui/react-streamdown'
import { code } from '@streamdown/code'
import { math } from '@streamdown/math'
import 'katex/dist/katex.min.css'
import './markdown-styles.css'
import {
  UserIcon,
  SendIcon,
  CopyIcon,
  CheckIcon,
  ArrowDownIcon,
  PaperclipIcon,
  ArrowDownToLineIcon,
  ArrowUpFromLineIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useAtomValue } from 'jotai'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { ComingSoonButton } from '@/components/shared/coming-soon-button'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { sessionTokenUsageAtom, type TokenUsage } from '@/lib/stores/chat-store'
import { GenericToolFallback, ToolFallbackPanel } from '@/components/chat/tool-ui/generic-tool-ui'
import { WittyLoadingMessage } from '@/components/chat/witty-loading'
import { ChatImage } from '@/components/chat/markdown-content'
import { formatRelativeShort } from '@/lib/utils/format-relative-time'

export { GenericToolFallback }

/** 메시지 메타에서 createdAt을 읽어 한국어 상대 시간을 표시 */
function MessageTimestamp() {
  const tCommon = useTranslations('common')
  const createdAt = useAssistantState(
    (s) => (s.message as { createdAt?: Date } | undefined)?.createdAt,
  )
  if (!createdAt) return null
  return (
    <span className="text-[10px] text-muted-foreground">
      {formatRelativeShort(createdAt, tCommon('yesterday'))}
    </span>
  )
}

/** StreamdownTextPrimitive는 MessagePrimitive 컨텍스트에서 자동으로 텍스트를 읽는다. */
function AssistantTextPart() {
  return (
    <div className="prose-chat py-1 text-sm leading-relaxed text-foreground">
      <StreamdownTextPrimitive
        plugins={{ code, math }}
        shikiTheme={['github-light', 'github-dark']}
        components={{
          img: ((props: React.ImgHTMLAttributes<HTMLImageElement>) => {
            const src = typeof props.src === 'string' ? props.src : undefined
            if (!src) return null
            return <ChatImage src={src} alt={props.alt ?? ''} />
          }) as never,
        }}
      />
    </div>
  )
}

/** tool-call 파트를 ToolFallbackPanel로 렌더링하는 래퍼 */
function ToolCallFallback({
  toolName,
  args,
  result,
  status,
}: {
  toolName: string
  args: Record<string, unknown>
  result?: unknown
  status: { type: string }
}) {
  const resolved =
    status.type === 'running'
      ? ('running' as const)
      : status.type === 'complete'
        ? ('complete' as const)
        : ('error' as const)
  return <ToolFallbackPanel toolName={toolName} args={args} result={result} status={resolved} />
}

/** flex order로 도구 호출(order-1)을 위, 텍스트(order-2)를 아래로 시각 재배치.
 * assistant-ui의 streaming/state는 그대로 유지되며 DOM 순서만 reorder된다.
 *
 * 모듈-level 컴포넌트로 정의해 components prop의 함수 reference가 매 render마다
 * 새로 생기지 않게 한다 — assistant-ui 내부 메모화가 깨져 token마다 자식이
 * 리마운트되면 streaming 텍스트가 화면에 표시되지 않는 문제를 방지.
 */
function OrderedTextPart() {
  return (
    <div className="order-2">
      <AssistantTextPart />
    </div>
  )
}

function OrderedToolFallback(props: {
  toolName: string
  args: Record<string, unknown>
  result?: unknown
  status: { type: string }
}) {
  return (
    <div className="order-1">
      <ToolCallFallback {...props} />
    </div>
  )
}

const ASSISTANT_PART_COMPONENTS = {
  Text: OrderedTextPart,
  tools: { Fallback: OrderedToolFallback },
} as const

function AssistantMessageParts() {
  return (
    <div className="flex flex-col">
      <MessagePrimitive.Content components={ASSISTANT_PART_COMPONENTS} />
    </div>
  )
}

/** 메시지가 running 상태일 때 메시지 위쪽에 absolute로 띄우는 loading row.
 *
 * 부모(AssistantMsg)의 `relative` 컨테이너 안에 absolute로 배치되므로 메시지
 * layout에 영향을 주지 않는다. 메시지가 끝나면 자동으로 사라지지만 답변
 * 텍스트의 위치는 흔들리지 않는다.
 */
function StreamingLoadingIndicator() {
  const isRunning = useAssistantState(
    (s) => (s.message?.status as { type?: string } | undefined)?.type === 'running',
  )
  if (!isRunning) return null
  // left-11 = avatar size-8(2rem) + gap-3(0.75rem) → avatar 우측에 정렬.
  // size 변경 시 동기화 필요.
  return <WittyLoadingMessage className="pointer-events-none absolute -top-5 left-11 px-1" />
}

/** 메시지 hover 시 표시되는 메타 row (시간 + 복사 버튼). 자식 순서로
 * 사용자/AI 메시지 쪽 정렬을 표현. */
function MessageMetaRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-1 flex items-center gap-2 opacity-0 transition-opacity group-hover:opacity-100">
      {children}
    </div>
  )
}

function CopyButton() {
  const [copied, setCopied] = useState(false)
  const t = useTranslations('chat.message')

  return (
    <ActionBarPrimitive.Copy
      copiedDuration={2000}
      onClick={() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
      className="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      aria-label={t('copyLabel')}
    >
      {copied ? (
        <>
          <CheckIcon className="size-3 text-emerald-500" />
          <span className="text-emerald-500">{t('copied')}</span>
        </>
      ) : (
        <>
          <CopyIcon className="size-3" />
          <span>{t('copy')}</span>
        </>
      )}
    </ActionBarPrimitive.Copy>
  )
}

export interface AssistantThreadProps {
  agentImageUrl?: string | null
  agentName?: string
  /** 토큰 바 표시에 사용할 모델명 */
  modelName?: string
  /** true이면 Composer 토큰 바 표시 */
  showTokenBar?: boolean
  /** 컴팩트 모드 (AssistantPanel용) — Composer 높이 축소 */
  compact?: boolean
  /** true이면 메시지 하단에 createdAt 시간 라벨 표시 */
  showMessageTimestamp?: boolean
  /** 빈 상태 커스텀 */
  emptyContent?: React.ReactNode
  /** 추가 도구 UI */
  toolUI?: readonly AssistantToolUI[]
}

export function AssistantThread({
  agentImageUrl,
  agentName,
  modelName,
  showTokenBar = false,
  compact = false,
  showMessageTimestamp = false,
  emptyContent,
  toolUI,
}: AssistantThreadProps) {
  const tChat = useTranslations('chat')
  const tPage = useTranslations('chat.page')

  return (
    <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col">
      <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto">
        <ThreadPrimitive.Empty>
          {emptyContent ?? (
            <div className="flex h-full items-center justify-center py-8 text-center text-muted-foreground">
              <p className="text-sm">{tPage('emptyState')}</p>
            </div>
          )}
        </ThreadPrimitive.Empty>

        <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-4">
          <ThreadPrimitive.Messages
            components={{
              UserMessage: function UserMsg() {
                return (
                  <div className="group relative flex justify-end gap-3">
                    <div className="flex max-w-[80%] flex-col items-end">
                      <div className="rounded-2xl bg-emerald-100 px-4 py-2.5 text-sm leading-relaxed text-emerald-950 dark:bg-emerald-900 dark:text-emerald-100">
                        <MessagePrimitive.Content />
                      </div>
                      <MessageMetaRow>
                        <CopyButton />
                        {showMessageTimestamp && <MessageTimestamp />}
                      </MessageMetaRow>
                    </div>
                    <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                      <UserIcon className="size-4" />
                    </div>
                  </div>
                )
              },
              AssistantMessage: function AssistantMsg() {
                return (
                  <div className="group relative flex gap-3">
                    {/* loading indicator를 absolute로 배치 — 메시지 layout 밖에 떠 있어
                        사라질 때 답변 텍스트가 점프하지 않도록 한다. */}
                    <StreamingLoadingIndicator />
                    <AgentAvatar
                      imageUrl={agentImageUrl ?? null}
                      name={agentName ?? tChat('defaultAgentName')}
                      size="sm"
                    />
                    <div className="min-w-0 flex-1">
                      <AssistantMessageParts />
                      <MessageMetaRow>
                        {showMessageTimestamp && <MessageTimestamp />}
                        <CopyButton />
                      </MessageMetaRow>
                    </div>
                  </div>
                )
              },
            }}
          />
        </div>

        <ThreadPrimitive.ViewportFooter>
          <ScrollToBottomButton />
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>

      {/* 도구 UI 등록 */}
      {toolUI?.map((ToolComponent, i) => (
        <ToolComponent key={i} />
      ))}

      {/* Composer */}
      <div className="mx-auto w-full max-w-3xl px-4 pb-4">
        <ThreadComposer modelName={modelName} showTokenBar={showTokenBar} compact={compact} />
      </div>
    </ThreadPrimitive.Root>
  )
}

function ScrollToBottomButton() {
  const isAtBottom = useThreadViewport((v) => v.isAtBottom)

  if (isAtBottom) return null

  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      <button
        type="button"
        className="mx-auto mb-2 flex size-8 items-center justify-center rounded-full border bg-background shadow-sm transition-opacity hover:bg-accent"
      >
        <ArrowDownIcon className="size-4" />
      </button>
    </ThreadPrimitive.ScrollToBottom>
  )
}

function ThreadComposer({
  modelName,
  showTokenBar,
  compact,
}: {
  modelName?: string
  showTokenBar?: boolean
  compact?: boolean
}) {
  const tc = useTranslations('common')
  const t = useTranslations('chat.input')
  const tokenUsage = useAtomValue(sessionTokenUsageAtom)
  const hasTokens = showTokenBar && (tokenUsage.inputTokens > 0 || tokenUsage.outputTokens > 0)

  return (
    <ComposerPrimitive.Root className="overflow-hidden rounded-2xl border border-input bg-background shadow-sm">
      {/* Model & Token bar */}
      {(modelName || hasTokens) && (
        <div className="flex items-center gap-3 border-b border-input/50 px-3.5 py-1.5 text-xs text-muted-foreground">
          {modelName && <span className="font-medium text-foreground/70">{modelName}</span>}
          {hasTokens && (
            <TokenBar tokenUsage={tokenUsage} showDivider={false} className="ml-auto" />
          )}
        </div>
      )}

      {/* Textarea */}
      <ComposerPrimitive.Input
        placeholder={t('placeholder')}
        submitMode="enter"
        className={cn(
          'w-full resize-none bg-transparent px-3.5 py-2.5 text-sm leading-relaxed outline-none',
          'placeholder:text-muted-foreground',
          'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
          compact ? 'min-h-[40px] max-h-[120px]' : 'min-h-[44px] max-h-[160px]',
        )}
        rows={1}
      />

      {/* Toolbar */}
      <div className="flex items-center justify-between px-2 py-1.5">
        <div className="flex items-center gap-1">
          <ComingSoonButton message={tc('comingSoon.fileAttach')} className="text-muted-foreground">
            <PaperclipIcon className="size-4" />
            <span className="sr-only">{tc('comingSoon.fileAttach')}</span>
          </ComingSoonButton>
        </div>
        <ComposerPrimitive.Send asChild>
          <Button type="submit" size="icon-sm" variant="emerald" className="rounded-full">
            <SendIcon className="size-4" />
            <span className="sr-only">{t('sendButton')}</span>
          </Button>
        </ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  )
}

function TokenBar({
  tokenUsage,
  showDivider,
  className,
}: {
  tokenUsage: TokenUsage
  showDivider: boolean
  className?: string
}) {
  return (
    <>
      {showDivider && <span className="text-border">·</span>}
      <span className={cn('flex items-center gap-1', className)}>
        <ArrowDownToLineIcon className="size-3" />
        {formatTokens(tokenUsage.inputTokens)}
      </span>
      <span className="flex items-center gap-1">
        <ArrowUpFromLineIcon className="size-3" />
        {formatTokens(tokenUsage.outputTokens)}
      </span>
      {tokenUsage.cost > 0 && (
        <>
          <span className="text-border">·</span>
          <span>{formatCost(tokenUsage.cost)}</span>
        </>
      )}
    </>
  )
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function formatCost(n: number): string {
  if (n < 0.01) return `$${n.toFixed(4)}`
  return `$${n.toFixed(2)}`
}
