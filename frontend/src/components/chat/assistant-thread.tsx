'use client'

import { useCallback, useMemo, useState } from 'react'
import {
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
  AttachmentPrimitive,
  ActionBarPrimitive,
  useThreadViewport,
  useAssistantState,
  useAui,
  type AssistantToolUI,
} from '@assistant-ui/react'
import { useQueryClient } from '@tanstack/react-query'
import { conversationsApi } from '@/lib/api/conversations'
import { conversationKeys } from '@/lib/hooks/use-conversations'
import { StreamdownTextPrimitive } from '@assistant-ui/react-streamdown'
import { math } from '@streamdown/math'
import { buildMarkdownComponents } from '@/components/chat/markdown-content'
import { CHAT_STREAMING_REMARK_PLUGINS } from '@/components/chat/markdown-plugins'
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
  PencilIcon,
  RotateCcwIcon,
  ThumbsUpIcon,
  ThumbsDownIcon,
  XIcon,
  FileIcon,
  ImageIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useAtomValue } from 'jotai'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { sessionTokenUsageAtom, type TokenUsage } from '@/lib/stores/chat-store'
import { GenericToolFallback, ToolFallbackPanel } from '@/components/chat/tool-ui/generic-tool-ui'
import { WittyLoadingMessage } from '@/components/chat/witty-loading'
import { TokenUsagePopover } from '@/components/chat/token-usage-popover'
import { ReconnectIndicator } from '@/components/chat/reconnect-indicator'
import { formatRelativeShort } from '@/lib/utils/format-relative-time'
import {
  BuilderAssistantMessage,
  BuilderAssistantMessageParts,
  BuilderComposer,
  BuilderUserEditComposer,
  BuilderUserMessage,
} from '@/components/chat/builder-overrides'
import { ImeSafeComposerInput } from '@/components/chat/ime-safe-composer-input'
import {
  ChatConversationContext,
  useChatConversationId,
} from '@/components/chat/conversation-context'

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

// CHAT_STREAMING_REMARK_PLUGINS는 모듈 레벨 상수다. 컴포넌트 body 안에서
// 새 배열을 만들면 streamdown 메모이즈 캐시가 깨져 리렌더 루프가 날 수 있다.
// remarkPlugins를 넘기면 streamdown 내장 기본값이 덮어써지므로, GFM은
// CHAT_STREAMING_REMARK_PLUGINS에서 직접 재주입한다.
// streamdown의 syntax highlight(@streamdown/code)는 우리 SyntaxHighlighter와
// 출력이 충돌하므로 제거 — math plugin만 유지.
const STREAMDOWN_PLUGINS = { math }
// 두 번 빌드해두고 useMemo 없이 isRunning에 따라 분기 — 매 렌더에서 새 객체를
// 만들지 않으면서도 isStreaming 동적으로 적용 가능. (true→partial mermaid를
// raw로 렌더, false→ message 종료 후 SVG 시도)
const MARKDOWN_COMPONENTS_STREAMING = buildMarkdownComponents({ isStreaming: true })
const MARKDOWN_COMPONENTS_FINAL = buildMarkdownComponents({ isStreaming: false })

/** StreamdownTextPrimitive는 MessagePrimitive 컨텍스트에서 자동으로 텍스트를 읽는다.
 *
 * isStreaming은 message status로 동적 detect — running 중에는 mermaid 등
 * 무거운 fenced 블록을 raw로 두고, message 종료 후에야 다이어그램 렌더 시도.
 */
function AssistantTextPart() {
  const isRunning = useAssistantState(
    (s) => (s.message?.status as { type?: string } | undefined)?.type === 'running',
  )
  const components = isRunning ? MARKDOWN_COMPONENTS_STREAMING : MARKDOWN_COMPONENTS_FINAL
  return (
    <div className="prose-chat py-1 text-sm leading-relaxed text-foreground">
      <StreamdownTextPrimitive
        plugins={STREAMDOWN_PLUGINS}
        remarkPlugins={CHAT_STREAMING_REMARK_PLUGINS}
        // Components 타입(react-markdown)과 streamdown 내부 타입이 호환되지 않아
        // never로 우회 — 런타임 동작은 동일.
        components={components as never}
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

function StreamingMessageLoadingIndicator({ className }: { className?: string }) {
  const isStreamingMessage = useIsStreamingMessage()
  if (!isStreamingMessage) return null
  return (
    <ThreadPrimitive.If running={true}>
      <WittyLoadingMessage className={cn('pointer-events-none mb-1 px-1', className)} />
    </ThreadPrimitive.If>
  )
}

function useIsStreamingMessage(): boolean {
  return useAssistantState((s) =>
    Boolean(
      (s.message?.metadata as { custom?: { isStreamingMessage?: boolean } } | undefined)?.custom
        ?.isStreamingMessage,
    ),
  )
}

/** 메시지 hover 시 표시되는 메타 row (시간 + 복사 버튼). 자식 순서로
 * 사용자/AI 메시지 쪽 정렬을 표현. */
function MessageMetaRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-1 flex min-h-7 max-w-full items-center gap-1 overflow-hidden opacity-0 transition-opacity group-hover:opacity-100">
      {children}
    </div>
  )
}

const MESSAGE_ACTION_CLASS =
  'inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40'

function CopyButton() {
  const [copied, setCopied] = useState(false)
  const t = useTranslations('chat.message')
  const label = copied ? t('copied') : t('copy')

  return (
    <ActionBarPrimitive.Copy
      copiedDuration={2000}
      onClick={() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
      className={MESSAGE_ACTION_CLASS}
      aria-label={t('copyLabel')}
      title={label}
    >
      {copied ? (
        <>
          <CheckIcon className="size-3 text-status-success" />
          <span className="sr-only">{t('copied')}</span>
        </>
      ) : (
        <>
          <CopyIcon className="size-3" />
          <span className="sr-only">{t('copy')}</span>
        </>
      )}
    </ActionBarPrimitive.Copy>
  )
}

/** P0-1a — pencil icon that flips a user message into an inline edit composer. */
function EditButton() {
  const t = useTranslations('chat.message')
  return (
    <ActionBarPrimitive.Edit
      className={MESSAGE_ACTION_CLASS}
      aria-label={t('edit')}
      title={t('edit')}
    >
      <PencilIcon className="size-3" />
      <span className="sr-only">{t('edit')}</span>
    </ActionBarPrimitive.Edit>
  )
}

/** P0-1b — regenerate the latest assistant turn. */
function RegenerateButton() {
  const t = useTranslations('chat.message')
  return (
    <ActionBarPrimitive.Reload
      className={MESSAGE_ACTION_CLASS}
      aria-label={t('regenerate')}
      title={t('regenerate')}
    >
      <RotateCcwIcon className="size-3" />
      <span className="sr-only">{t('regenerate')}</span>
    </ActionBarPrimitive.Reload>
  )
}

interface BranchMeta {
  branches?: string[]
  siblingCheckpointIds?: string[]
  activeBranchId?: string
  branchCheckpointId?: string | null
  branchIndex?: number | null
  branchTotal?: number | null
}

/** M-CHAT1b — `<1/2>` style branch picker.
 *
 * Backend (``thread_branch_service``) sorts siblings oldest→newest by
 * checkpoint id and stamps the active message with explicit
 * ``branchIndex/branchTotal`` so we never have to ``indexOf(activeId)``
 * (that path miscounted when the active message wasn't lex-first; HOTFIX2).
 *
 * Hidden when a message has no siblings. */
/** P1-B — true when this message has a renderable branch picker (>=2 siblings
 * + matching checkpoint count). Pulled out so the guard intent is named. */
function canRenderBranchPicker(
  conversationId: string | null,
  meta: BranchMeta,
): meta is BranchMeta & {
  branchIndex: number
  branchTotal: number
  siblingCheckpointIds: string[]
} {
  const siblingCheckpoints = meta.siblingCheckpointIds ?? []
  const { branchIndex, branchTotal } = meta
  return (
    !!conversationId &&
    branchIndex != null &&
    branchTotal != null &&
    branchTotal >= 2 &&
    siblingCheckpoints.length === branchTotal
  )
}

function BranchPicker() {
  const t = useTranslations('chat.branch')
  const conversationId = useChatConversationId()
  const queryClient = useQueryClient()
  const [pendingCheckpointId, setPendingCheckpointId] = useState<string | null>(null)
  const meta = useAssistantState(
    (s) =>
      ((s.message?.metadata as { custom?: BranchMeta } | undefined)?.custom ?? {}) as BranchMeta,
  )
  const siblingCheckpoints = useMemo(
    () => meta.siblingCheckpointIds ?? [],
    [meta.siblingCheckpointIds],
  )

  const switchTo = useCallback(
    async (targetIdx: number) => {
      if (!conversationId || pendingCheckpointId) return
      const checkpointId = siblingCheckpoints[targetIdx]
      if (!checkpointId) {
        console.warn('[BranchPicker] missing checkpoint id for sibling idx', targetIdx)
        return
      }
      setPendingCheckpointId(checkpointId)
      try {
        await conversationsApi.switchBranch(conversationId, checkpointId)
        await queryClient.refetchQueries({
          queryKey: conversationKeys.messages(conversationId),
          type: 'active',
        })
      } catch (err) {
        console.error('[BranchPicker] switch failed', err)
      } finally {
        setPendingCheckpointId(null)
      }
    },
    [conversationId, pendingCheckpointId, queryClient, siblingCheckpoints],
  )

  if (!canRenderBranchPicker(conversationId, meta)) return null
  // canRenderBranchPicker guarantees conversationId is non-null + the index/
  // total/checkpoint counts line up. The casts below are narrowing helpers
  // because the type guard only narrows ``meta``.
  const branchTotal = meta.branchTotal as number
  const currentIdx = meta.branchIndex as number

  const total = branchTotal
  const display = currentIdx + 1
  const isSwitching = pendingCheckpointId !== null
  return (
    <span className="inline-flex items-center gap-0.5 text-[10px] tabular-nums text-muted-foreground">
      <button
        type="button"
        className="inline-flex size-4 items-center justify-center rounded hover:bg-accent disabled:opacity-30"
        disabled={isSwitching || currentIdx <= 0}
        onClick={() => void switchTo(currentIdx - 1)}
        aria-label={t('previous')}
      >
        <ChevronLeftIcon className="size-3" />
      </button>
      <span className="px-1">
        {display}/{total}
      </span>
      <button
        type="button"
        className="inline-flex size-4 items-center justify-center rounded hover:bg-accent disabled:opacity-30"
        disabled={isSwitching || currentIdx >= total - 1}
        onClick={() => void switchTo(currentIdx + 1)}
        aria-label={t('next')}
      >
        <ChevronRightIcon className="size-3" />
      </button>
    </span>
  )
}

/** P0-1c — thumbs up/down. The active state pulls from
 * ``message.metadata.submittedFeedback`` which we hydrate in ``convertMessage``
 * (and assistant-ui keeps in sync after each ``adapter.feedback.submit``). */
function FeedbackButtons() {
  const t = useTranslations('chat.message')
  const submitted = useAssistantState(
    (s) =>
      (s.message?.metadata as { submittedFeedback?: { type: 'positive' | 'negative' } } | undefined)
        ?.submittedFeedback?.type,
  )
  return (
    <>
      <ActionBarPrimitive.FeedbackPositive
        className={cn(
          MESSAGE_ACTION_CLASS,
          submitted === 'positive'
            ? 'text-primary-strong'
            : 'text-muted-foreground hover:text-foreground',
        )}
        aria-label={t('feedbackUp')}
        title={t('feedbackUp')}
      >
        <ThumbsUpIcon className="size-3" />
      </ActionBarPrimitive.FeedbackPositive>
      <ActionBarPrimitive.FeedbackNegative
        className={cn(
          MESSAGE_ACTION_CLASS,
          submitted === 'negative'
            ? 'text-status-warn'
            : 'text-muted-foreground hover:text-foreground',
        )}
        aria-label={t('feedbackDown')}
        title={t('feedbackDown')}
      >
        <ThumbsDownIcon className="size-3" />
      </ActionBarPrimitive.FeedbackNegative>
    </>
  )
}

/** P0-1a — inline editor for a user message (replaces the bubble while
 * ``MessagePrimitive.If editing`` is true). */
function UserMessageEditor() {
  const t = useTranslations('chat.message')
  return (
    <ComposerPrimitive.Root className="flex flex-col gap-2 rounded-2xl border bg-background p-2 shadow-sm">
      <ImeSafeComposerInput
        className="min-h-[40px] w-full resize-none bg-transparent px-2 py-1 text-sm leading-relaxed outline-none"
        autoFocus
      />
      <div className="flex items-center justify-end gap-1">
        <ComposerPrimitive.Cancel asChild>
          <Button type="button" size="sm" variant="ghost">
            {t('editCancel')}
          </Button>
        </ComposerPrimitive.Cancel>
        <ComposerPrimitive.Send asChild>
          <Button type="submit" size="sm">
            {t('editSave')}
          </Button>
        </ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  )
}

export interface AssistantThreadProps {
  agentImageUrl?: string | null
  /** true이면 agentImageUrl을 frontend public 자산으로 처리 (API_BASE prepend X) */
  agentImagePublicAsset?: boolean
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
  /** P1-7 — true이면 composer에 첨부 파일 버튼/미리보기 표시.
   * AttachmentAdapter는 useChatRuntime에 별도로 전달되어야 한다. */
  enableAttachments?: boolean
  /** M-CHAT1b — when set, BranchPicker (`<1/2>`) renders inside the meta row
   * for any message whose backend payload reported sibling branches. The id
   * is used to POST `/messages/switch-branch` and invalidate the messages
   * query on click. */
  conversationId?: string
  /** Visual variant. Default = 기존 동작 (4개 채팅 페이지 공유). builder = 빌더 전용 리스킨
   *  (mint user bubble, bare 38×38 mascot, no user avatar, mint focus composer, 모델 메타 라벨). */
  variant?: 'default' | 'builder'
  /** variant='builder' 시 Composer 메타 라벨 (예: `대화형 에이전트 빌더 · GPT-4 Turbo`). */
  builderModelLabel?: string
  /** variant='builder' 시 Assistant 메시지 이름줄 보조 라벨. 기본 `에이전트 빌더`. */
  builderAgentSubtitle?: string
}

export function AssistantThread({
  agentImageUrl,
  agentImagePublicAsset = false,
  agentName,
  modelName,
  showTokenBar = false,
  compact = false,
  showMessageTimestamp = false,
  emptyContent,
  toolUI,
  enableAttachments = false,
  conversationId,
  variant = 'default',
  builderModelLabel,
  builderAgentSubtitle,
}: AssistantThreadProps) {
  const tChat = useTranslations('chat')
  const tPage = useTranslations('chat.page')
  const isBuilder = variant === 'builder'

  const messageComponents = useMemo(
    () => ({
      UserMessage: function UserMsg() {
        const metaRow = (
          <MessageMetaRow>
            <BranchPicker />
            <EditButton />
            <CopyButton />
            {showMessageTimestamp && <MessageTimestamp />}
          </MessageMetaRow>
        )
        if (isBuilder) {
          return <BuilderUserMessage metaRow={metaRow} />
        }
        return (
          <div className="group relative flex justify-end gap-3 [contain-intrinsic-size:0_96px] [content-visibility:auto]">
            <div className="flex w-full max-w-[80%] flex-col items-end">
              <div className="rounded-2xl bg-emerald-100 px-4 py-2.5 text-sm leading-relaxed text-emerald-950 dark:bg-emerald-900 dark:text-emerald-100">
                <MessagePrimitive.Content />
              </div>
              {metaRow}
            </div>
            <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <UserIcon className="size-4" />
            </div>
          </div>
        )
      },
      UserEditComposer: function UserEdit() {
        if (isBuilder) {
          return <BuilderUserEditComposer />
        }
        return (
          <div className="flex justify-end gap-3">
            <div className="flex w-full max-w-[80%] flex-col items-end">
              <UserMessageEditor />
            </div>
            <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <UserIcon className="size-4" />
            </div>
          </div>
        )
      },
      AssistantMessage: function AssistantMsg() {
        const isStreamingMessage = useIsStreamingMessage()
        const metaRow = (
          <MessageMetaRow>
            {showMessageTimestamp && <MessageTimestamp />}
            <BranchPicker />
            <CopyButton />
            <RegenerateButton />
            <FeedbackButtons />
            <TokenUsagePopover />
          </MessageMetaRow>
        )
        if (isBuilder) {
          return (
            <BuilderAssistantMessage metaRow={metaRow} agentSubtitle={builderAgentSubtitle}>
              <StreamingMessageLoadingIndicator />
              <BuilderAssistantMessageParts />
            </BuilderAssistantMessage>
          )
        }
        return (
          <div
            className={cn(
              'group relative flex gap-3',
              !isStreamingMessage && '[contain-intrinsic-size:0_180px] [content-visibility:auto]',
            )}
          >
            <StreamingMessageLoadingIndicator className="absolute -top-5 left-11 mb-0" />
            <AgentAvatar
              imageUrl={agentImageUrl ?? null}
              name={agentName ?? tChat('defaultAgentName')}
              size="sm"
              publicAsset={agentImagePublicAsset}
            />
            <div className="min-w-0 flex-1">
              <AssistantMessageParts />
              {metaRow}
            </div>
          </div>
        )
      },
    }),
    [
      agentImagePublicAsset,
      agentImageUrl,
      agentName,
      builderAgentSubtitle,
      isBuilder,
      showMessageTimestamp,
      tChat,
    ],
  )

  return (
    <ChatConversationContext.Provider value={conversationId ?? null}>
      <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col">
        <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto">
          <ThreadPrimitive.Empty>
            {emptyContent ?? (
              <div className="flex h-full items-center justify-center py-8 text-center text-muted-foreground">
                <p className="text-sm">{tPage('emptyState')}</p>
              </div>
            )}
          </ThreadPrimitive.Empty>

          <div
            className={cn(
              'mx-auto w-full px-4 py-4',
              isBuilder ? 'max-w-[880px] space-y-6' : 'max-w-3xl space-y-4',
            )}
          >
            <ThreadPrimitive.Messages components={messageComponents} />
          </div>

          <ThreadPrimitive.ViewportFooter>
            <ScrollToBottomButton />
          </ThreadPrimitive.ViewportFooter>
        </ThreadPrimitive.Viewport>

        {/* 도구 UI 등록 */}
        {toolUI?.map((ToolComponent, i) => (
          <ToolComponent key={i} />
        ))}

        <ReconnectIndicator />

        {/* Composer */}
        {isBuilder ? (
          <BuilderComposer modelLabel={builderModelLabel} />
        ) : (
          <div className="mx-auto w-full max-w-3xl px-4 pb-4">
            <ThreadComposer
              modelName={modelName}
              showTokenBar={showTokenBar}
              compact={compact}
              enableAttachments={enableAttachments}
            />
          </div>
        )}
      </ThreadPrimitive.Root>
    </ChatConversationContext.Provider>
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
  enableAttachments = false,
}: {
  modelName?: string
  showTokenBar?: boolean
  compact?: boolean
  enableAttachments?: boolean
}) {
  const t = useTranslations('chat.input')
  const tMsg = useTranslations('chat.message')
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

      {/* Attachment preview row (P1-7) — only renders when at least one
          attachment is staged; hidden otherwise so the composer stays compact. */}
      {enableAttachments && (
        <ComposerPrimitive.Attachments
          components={{
            Attachment: AttachmentChip,
          }}
        />
      )}

      {/* Textarea */}
      <ImeSafeComposerInput
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
          {enableAttachments && (
            <ComposerPrimitive.AddAttachment asChild>
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                className="text-muted-foreground"
                aria-label={tMsg('attach')}
              >
                <PaperclipIcon className="size-4" />
              </Button>
            </ComposerPrimitive.AddAttachment>
          )}
        </div>
        <ThreadPrimitive.If running={false}>
          <ComposerPrimitive.Send asChild>
            <Button type="submit" size="icon-sm" className="rounded-full">
              <SendIcon className="size-4" />
              <span className="sr-only">{t('sendButton')}</span>
            </Button>
          </ComposerPrimitive.Send>
        </ThreadPrimitive.If>
        <ThreadPrimitive.If running={true}>
          <StopButton />
        </ThreadPrimitive.If>
      </div>
    </ComposerPrimitive.Root>
  )
}

/** Stop 버튼 — 진행 중인 응답을 취소.
 *
 * `aui.thread().cancelRun()`을 호출하면 ExternalStoreRuntime의 onCancel 이 트리거되어
 * useChatRuntime의 abortRef.current.abort() 가 실행된다 (AbortController 경로).
 * 시각: 32px 높이 pill, 9×9 dark square + "중단" 라벨.
 */
function StopButton() {
  const aui = useAui()
  const tMsg = useTranslations('chat.message')
  const handleStop = () => {
    try {
      aui.thread().cancelRun()
    } catch (err) {
      console.warn('[StopButton] cancelRun error:', err)
    }
  }
  return (
    <button
      type="button"
      onClick={handleStop}
      aria-label={tMsg('stop')}
      className="inline-flex h-8 items-center gap-1.5 rounded-[9px] border border-input bg-background px-3 text-[12.5px] font-medium text-foreground/80 transition-colors hover:bg-accent"
    >
      <span aria-hidden className="block size-[9px] rounded-sm bg-foreground/80" />
      {tMsg('stop')}
    </button>
  )
}

/** P1-7 — preview chip for one staged attachment. Renders inside the
 * ``ComposerPrimitive.Attachments`` slot which already provides per-attachment
 * context, so we just read from ``useAssistantState(s.attachment)``. */
function AttachmentChip() {
  const tMsg = useTranslations('chat.message')
  const attachment = useAssistantState(
    (s) =>
      (s as { attachment?: { name: string; contentType?: string; status?: { type: string } } })
        .attachment,
  )
  if (!attachment) return null
  const isImage = attachment.contentType?.startsWith('image/')
  const isUploading = attachment.status?.type === 'running'

  return (
    <AttachmentPrimitive.Root className="m-1 inline-flex min-w-0 items-center gap-2 rounded-md border bg-muted/40 px-2 py-1 text-xs">
      <span className="flex size-5 shrink-0 items-center justify-center text-muted-foreground">
        {isImage ? <ImageIcon className="size-3.5" /> : <FileIcon className="size-3.5" />}
      </span>
      <span className="max-w-[180px] truncate">
        <AttachmentPrimitive.Name />
      </span>
      {isUploading && (
        <span className="text-[10px] text-muted-foreground">{tMsg('attachmentUploading')}</span>
      )}
      <AttachmentPrimitive.Remove asChild>
        <button
          type="button"
          className="ml-1 inline-flex size-4 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label={tMsg('attachmentRemove')}
        >
          <XIcon className="size-3" />
        </button>
      </AttachmentPrimitive.Remove>
    </AttachmentPrimitive.Root>
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
