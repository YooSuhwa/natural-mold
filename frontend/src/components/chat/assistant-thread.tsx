'use client'

import { type FC, useState } from 'react'
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

export { GenericToolFallback }

const UserMessage: FC = () => (
  <div className="group relative flex gap-3 justify-end">
    <div className="max-w-[80%]">
      <div className="rounded-2xl bg-emerald-100 px-4 py-2.5 text-sm leading-relaxed text-emerald-950 dark:bg-emerald-900 dark:text-emerald-100">
        <MessagePrimitive.Content />
      </div>
      {/* 복사 버튼 — hover 시 표시 */}
      <div className="flex justify-end mt-1 opacity-0 transition-opacity group-hover:opacity-100">
        <CopyButton />
      </div>
    </div>
    <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
      <UserIcon className="size-4" />
    </div>
  </div>
)

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

/** 표준 메시지 파트 렌더러 — 텍스트 + 도구 UI 모두 표시.
 *
 * Empty 슬롯은 의도적으로 비워둔다. assistant-ui의 Empty는 컨텐츠 마지막에
 * 노출되어 도구 호출 박스 아래에 loading 메시지가 표시되는데, 사용자 UX
 * 관점에선 도구 박스 위에 보이는 게 자연스럽다.  StreamingLoadingIndicator를
 * AssistantMessage 상단에 별도 배치해 위치를 잡는다.
 */
function AssistantMessageParts() {
  return (
    <MessagePrimitive.Content
      components={{
        Text: AssistantTextPart,
        tools: { Fallback: ToolCallFallback },
      }}
    />
  )
}

/** 메시지가 running 상태일 때 도구 박스 위쪽에 표시되는 loading row.
 *
 * 메시지가 끝나면 status.type !== 'running'이 되어 자동으로 사라지므로 과거
 * 메시지에는 영향 없음.
 */
function StreamingLoadingIndicator() {
  const isRunning = useAssistantState(
    (s) => (s.message?.status as { type?: string } | undefined)?.type === 'running',
  )
  if (!isRunning) return null
  return <WittyLoadingMessage className="px-1 pb-1" />
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
  emptyContent,
  toolUI,
}: AssistantThreadProps) {
  const tChat = useTranslations('chat')
  const tPage = useTranslations('chat.page')

  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto">
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
              UserMessage,
              AssistantMessage: function AssistantMsg() {
                return (
                  <div className="group flex gap-3">
                    <AgentAvatar
                      imageUrl={agentImageUrl ?? null}
                      name={agentName ?? tChat('defaultAgentName')}
                      size="sm"
                    />
                    <div className="min-w-0 flex-1">
                      {/* 도구 호출 박스 위에 streaming indicator 표시 (UX) */}
                      <StreamingLoadingIndicator />
                      <AssistantMessageParts />
                      {/* 복사 버튼 — hover 시 표시 */}
                      <div className="mt-1 opacity-0 transition-opacity group-hover:opacity-100">
                        <CopyButton />
                      </div>
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
          {hasTokens && <TokenBar tokenUsage={tokenUsage} showDivider={!!modelName} />}
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
          <Button type="submit" size="icon-sm" className="rounded-full transition-all">
            <SendIcon className="size-4" />
            <span className="sr-only">{t('sendButton')}</span>
          </Button>
        </ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  )
}

function TokenBar({ tokenUsage, showDivider }: { tokenUsage: TokenUsage; showDivider: boolean }) {
  return (
    <>
      {showDivider && <span className="text-border">·</span>}
      <span className="flex items-center gap-1">
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
