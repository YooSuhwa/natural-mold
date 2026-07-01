'use client'

import {
  createContext,
  lazy,
  Suspense,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
  type UIEvent,
} from 'react'
import {
  AuiIf,
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
  AttachmentPrimitive,
  ActionBarPrimitive,
  useThreadViewport,
  useAuiState,
  useAui,
  type AssistantDataUI,
  type AssistantToolUI,
  type EnrichedPartState,
} from '@assistant-ui/react'
import { useQueryClient } from '@tanstack/react-query'
import { conversationsApi } from '@/lib/api/conversations'
import { conversationKeys } from '@/lib/hooks/use-conversations'
import { StreamdownTextPrimitive } from '@assistant-ui/react-streamdown'
import { math } from '@streamdown/math'
import { buildMarkdownComponents } from '@/components/chat/markdown-components'
import { CHAT_STREAMING_REMARK_PLUGINS } from '@/components/chat/markdown-streaming-plugins'
import 'katex/dist/katex.min.css'
import './markdown-styles.css'
import {
  SendIcon,
  CopyIcon,
  CheckIcon,
  ArrowDownIcon,
  PaperclipIcon,
  ArrowDownToLineIcon,
  ArrowUpFromLineIcon,
  PencilIcon,
  RotateCcwIcon,
  AlertTriangleIcon,
  ThumbsUpIcon,
  ThumbsDownIcon,
  XIcon,
  FileIcon,
  FolderOpenIcon,
  ImageIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CoinsIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useAtomValue, useSetAtom } from 'jotai'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { UserAvatar } from '@/components/auth/UserAvatar'
import type { User } from '@/lib/types/user'
import {
  chatCancelInFlightAtom,
  latestTurnUsageAtom,
  pendingEditBranchPickerSuppressionAtom,
  sessionTokenUsageAtom,
  type TokenUsage,
} from '@/lib/stores/chat-store'
import { GenericToolFallback, ToolFallbackPanel } from '@/components/chat/tool-ui/generic-tool-ui'
import { ToolGroupContainer } from '@/components/chat/tool-ui/tool-group-container'
import { GroupedApprovalCard } from '@/components/chat/tool-ui/grouped-approval-card'
import { ContextWindowGauge } from '@/components/chat/context-window-gauge'
import {
  groupAssistantParts,
  isGroupToolNode,
  groupToolName,
  type GroupedRenderInfo,
} from '@/lib/chat/group-assistant-parts'
import { StreamingMessageLoadingIndicator } from '@/components/chat/assistant-message-loading'
import { UserMessageAttachments } from '@/components/chat/message-attachments'
import { CompactionSummary } from '@/components/chat/compaction-summary'
import type { CompactionMarker } from '@/lib/chat/langgraph-runtime/compaction-events'
import type { TerminalNoticeStatus } from '@/lib/chat/langgraph-runtime/terminal-notice'
import { TokenUsagePopover } from '@/components/chat/token-usage-popover'
import { ReconnectIndicator } from '@/components/chat/reconnect-indicator'
import { formatRelativeShort } from '@/lib/utils/format-relative-time'
import { formatCompactCount, formatDisplayUsd } from '@/lib/utils/display-format'
import { reportClientError, reportClientWarning } from '@/lib/logging/client-logger'
import { ImeSafeComposerInput } from '@/components/chat/ime-safe-composer-input'
import {
  MessageEditComposerInput,
  MessageEditComposerRoot,
  useMessageEditComposerControls,
} from '@/components/chat/message-edit-composer'
import {
  ChatConversationContext,
  useChatConversationId,
} from '@/components/chat/conversation-context'
import { useInvalidateFilesOnRunComplete } from '@/components/chat/use-files-run-sync'
import { isThreadViewportAtBottom } from '@/components/chat/scroll-bottom'
import { copyTextToClipboard, getMessageCopyText } from '@/components/chat/message-copy'
import { selectMessageArtifactsFromMessage } from '@/components/chat/message-artifact-metadata'
import { useRecordArtifactOpened } from '@/lib/hooks/use-artifact-library'
import { selectChatArtifactAtom } from '@/lib/stores/chat-artifacts'
import {
  chatRightRailAtom,
  isArtifactPreviewOpen,
  toggleArtifactPreviewRailState,
} from '@/lib/stores/chat-right-rail'
import type { ArtifactSummary } from '@/lib/types'
import type { DeepAgentsStateSnapshot } from '@/lib/chat/langgraph-runtime/deepagents-state'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'
import { dispatchMoldyBranchSwitched } from '@/lib/chat/langgraph-runtime/branch-switch-events'

export { GenericToolFallback }

const BuilderAssistantMessage = lazy(() =>
  import('@/components/chat/builder-overrides').then((m) => ({
    default: m.BuilderAssistantMessage,
  })),
)
const BuilderAssistantMessageParts = lazy(() =>
  import('@/components/chat/builder-overrides').then((m) => ({
    default: m.BuilderAssistantMessageParts,
  })),
)
const BuilderComposer = lazy(() =>
  import('@/components/chat/builder-overrides').then((m) => ({ default: m.BuilderComposer })),
)
const BuilderUserEditComposer = lazy(() =>
  import('@/components/chat/builder-overrides').then((m) => ({
    default: m.BuilderUserEditComposer,
  })),
)
const BuilderUserMessage = lazy(() =>
  import('@/components/chat/builder-overrides').then((m) => ({ default: m.BuilderUserMessage })),
)

function BuilderMessageFallback() {
  return <div className="min-h-12" aria-hidden />
}

function BuilderComposerFallback() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <div className="moldy-card h-20 animate-pulse" aria-hidden />
    </div>
  )
}

/** 메시지 메타에서 createdAt을 읽어 한국어 상대 시간을 표시 */
function MessageTimestamp() {
  const tCommon = useTranslations('common')
  const createdAt = useAuiState((s) => (s.message as { createdAt?: Date } | undefined)?.createdAt)
  if (!createdAt) return null
  // 액션 아이콘 클러스터와 시각(정보)을 구분 — 끝에 두고 약간 떨어뜨린다.
  return (
    <span className="ml-1 shrink-0 tabular-nums moldy-ui-micro text-muted-foreground">
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
  const isRunning = useAuiState(
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

/** order-1 자리에 개별 tool-call 한 줄(등록 per-tool UI 또는 폴백)을 렌더.
 * 그룹 컨테이너(ToolGroupContainer)와 동일한 order-1을 써서 시각 위치를 통일. */
function OrderedToolCall({
  toolUI,
  toolName,
  args,
  result,
  status,
}: {
  toolUI: ReactNode
  toolName: string
  args: Record<string, unknown>
  result?: unknown
  status: { type: string }
}) {
  return (
    <div className="order-1">
      {toolUI ?? (
        <ToolCallFallback toolName={toolName} args={args} result={result} status={status} />
      )}
    </div>
  )
}

// ── 범용 tool-call 그룹핑 (공식 MessagePrimitive.GroupedParts) ─────────────
//
// 연속 같은 도구 호출(N≥2)을 1개 ToolGroupContainer로 묶는다. N=1·비-tool part는
// 기존과 동일하게 개별 렌더. groupBy/render fn은 모듈 레벨 const로 둬서
// assistant-ui 내부 메모화(identity 기반)가 매 토큰마다 깨지지 않게 한다 —
// 위 OrderedTextPart 주석과 같은 이유로 streaming 표시 안정성에 필요하다.
//
// groupBy/노드 판별은 빌더 렌더(builder-overrides.tsx)와 공유하므로
// `group-assistant-parts.ts`에 두고, 각 표면의 leaf 비주얼만 render fn에서 분기한다.

// 기존 테스트(assistant-thread-grouping.test)가 이 모듈에서 import하므로 re-export.
export { groupAssistantParts }

/** GroupedParts의 노드/leaf를 그린다. group-tool 노드는 N≥2면 컨테이너, N=1이면
 * 개별 패스스루. leaf는 MessagePrimitive.Parts 기본 동작을 재현한다(아래 default 주석). */
export function renderGroupedAssistantPart({ part, children }: GroupedRenderInfo): ReactNode {
  if (isGroupToolNode(part)) {
    const running = part.status?.type === 'running'
    // N=1은 컨테이너 없이 개별 tool-call과 동일한 order-1로 통과. buildGroupTree는
    // 단일 tool-call도 그룹 노드로 감싸므로 여기서 임계값(N<2)을 처리한다.
    if (part.indices.length < 2) {
      return <div className="order-1">{children}</div>
    }
    // 승인 카드는 generic 그룹 컨테이너 대신 전용 "승인 대기 N건 + 모두 승인" 컨테이너로.
    if (groupToolName(part) === 'request_approval') {
      return (
        <div className="order-1">
          <GroupedApprovalCard count={part.indices.length}>{children}</GroupedApprovalCard>
        </div>
      )
    }
    // running→펼침/done→접힘은 key remount로 달성한다(CollapsiblePill은 uncontrolled).
    return (
      <div className="order-1">
        <ToolGroupContainer
          key={running ? 'running' : 'done'}
          toolName={groupToolName(part)}
          count={part.indices.length}
          running={running}
          indices={part.indices}
        >
          {children}
        </ToolGroupContainer>
      </div>
    )
  }

  switch (part.type) {
    case 'text':
      return <OrderedTextPart />
    case 'tool-call': {
      const leaf = part as Extract<EnrichedPartState, { type: 'tool-call' }>
      return (
        <OrderedToolCall
          toolUI={leaf.toolUI}
          toolName={leaf.toolName}
          args={leaf.args as Record<string, unknown>}
          result={leaf.result}
          status={leaf.status}
        />
      )
    }
    case 'data': {
      // MessagePrimitive.Parts의 data 기본값은 등록된 data renderer(우리: reasoning).
      // order wrapper 없이(=order-0) 렌더 → 기존 동작대로 tool-call/text보다 앞에 표시.
      const leaf = part as Extract<EnrichedPartState, { type: 'data' }>
      return leaf.dataRendererUI
    }
    case 'indicator':
      // indicator="never"라 발화하지 않지만 방어적으로 null. 로딩 인디케이터는
      // AssistantMsg가 StreamingMessageLoadingIndicator로 별도 렌더한다.
      return null
    default:
      // image/file/source/reasoning 등: 우리 앱은 Parts에 Text/tools.Fallback만
      // 넘겼고 나머지는 Parts 기본값이 전부 null을 반환한다. 그 동작을 그대로 재현.
      return null
  }
}

function AssistantMessageParts() {
  return (
    <div className="flex flex-col">
      <MessagePrimitive.GroupedParts groupBy={groupAssistantParts} indicator="never">
        {renderGroupedAssistantPart}
      </MessagePrimitive.GroupedParts>
    </div>
  )
}

function useMessageArtifacts(): ArtifactSummary[] {
  return useAuiState((s) => selectMessageArtifactsFromMessage(s.message))
}

function AssistantCompactionMarker() {
  // Selector returns the marker object (reference-stable while the converted
  // message is unchanged) or the null constant — never a fresh object, so this
  // can't loop-render `useAuiState`.
  const compaction = useAuiState(
    (s) =>
      (s.message?.metadata as { custom?: { compaction?: CompactionMarker } } | undefined)?.custom
        ?.compaction ?? null,
  )
  if (!compaction) return null
  return <CompactionSummary offloadPath={compaction.offloadPath} className="mt-2" />
}

function AssistantArtifactCards() {
  const artifacts = useMessageArtifacts()
  const conversationId = useChatConversationId()
  const selectArtifact = useSetAtom(selectChatArtifactAtom)
  const setRightRail = useSetAtom(chatRightRailAtom)
  const rightRail = useAtomValue(chatRightRailAtom)
  const openedMutation = useRecordArtifactOpened()
  const tArtifacts = useTranslations('chat.rightRail.artifacts')
  const tMessageArtifacts = useTranslations('chat.message.artifacts')

  if (artifacts.length === 0) return null

  const openArtifact = (artifact: ArtifactSummary) => {
    const targetConversationId = conversationId ?? artifact.conversation_id
    const nextState = toggleArtifactPreviewRailState(rightRail, {
      conversationId: targetConversationId,
      artifactId: artifact.id,
    })
    if (!isArtifactPreviewOpen(rightRail, targetConversationId, artifact.id)) {
      selectArtifact({ conversationId: targetConversationId, artifactId: artifact.id })
      openedMutation.mutate(artifact.id)
    }
    setRightRail(nextState)
  }

  return (
    <div className="mt-2 flex max-w-xl flex-col gap-2">
      {artifacts.map((artifact) => {
        const isImage = artifact.artifact_kind === 'image'
        const extension = artifact.extension?.toUpperCase()
        return (
          <button
            key={artifact.id}
            type="button"
            className="moldy-chat-card moldy-card-hover flex w-full items-center gap-3 px-3 py-3 text-left"
            aria-label={tMessageArtifacts('openLabel', { name: artifact.display_name })}
            onClick={() => openArtifact(artifact)}
          >
            <span className="flex size-12 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-foreground">
              {isImage ? <ImageIcon className="size-5" /> : <FileIcon className="size-5" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-foreground">
                {artifact.display_name}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                {tArtifacts(`kinds.${artifact.artifact_kind}`)}
                {extension ? ` · ${extension}` : ''}
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** 메시지 hover 시 표시되는 메타 row (시간 + 복사 버튼). 자식 순서로
 * 사용자/AI 메시지 쪽 정렬을 표현. */
function MessageMetaRow({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="mt-1 flex min-h-7 max-w-full items-center gap-0.5 overflow-hidden opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
      data-moldy-message-meta-row="true"
    >
      {children}
    </div>
  )
}

const MESSAGE_ACTION_CLASS =
  'inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40'

function CopyButton() {
  const [copied, setCopied] = useState(false)
  const [isCopying, setIsCopying] = useState(false)
  const t = useTranslations('chat.message')
  const label = copied ? t('copied') : t('copy')
  const copyText = useAuiState((s) => getMessageCopyText(s.message?.content))
  const isAssistantRunning = useAuiState(
    (s) => s.message?.role === 'assistant' && s.message.status?.type === 'running',
  )
  const disabled = !copyText || isCopying || isAssistantRunning

  const handleCopy = useCallback(async () => {
    if (disabled) return
    try {
      setIsCopying(true)
      await copyTextToClipboard(copyText)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      reportClientWarning('CopyButton', 'failed to copy message text', err)
    } finally {
      setIsCopying(false)
    }
  }, [copyText, disabled])

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      disabled={disabled}
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
    </button>
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

const RETRY_BUTTON_CLASS =
  'inline-flex items-center gap-1.5 self-start rounded-md px-2 py-1 text-xs font-medium underline-offset-2 transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-40'

/** G2 — retry a failed run. Reuses ``ActionBarPrimitive.Reload`` so assistant-ui
 * forwards the failed bubble's parent (the last user turn); ``checkpointForReload``
 * then forks from that user checkpoint and re-runs. Lives inside the error bubble
 * so it stays visible (the hover ``MessageMetaRow`` would hide it). */
function RetryButton() {
  const t = useTranslations('chat.message')
  return (
    <ActionBarPrimitive.Reload
      className={RETRY_BUTTON_CLASS}
      aria-label={t('retry')}
      title={t('retry')}
    >
      <RotateCcwIcon className="size-3.5 shrink-0" />
      <span>{t('retry')}</span>
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
  moldyBranchPickerDisplayOnly?: boolean
  moldySuppressBranchPicker?: boolean
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
} {
  const siblingCheckpoints = meta.siblingCheckpointIds ?? []
  const { branchIndex, branchTotal } = meta
  const hasBranchNumbers =
    typeof branchIndex === 'number' && typeof branchTotal === 'number' && branchTotal >= 2
  return (
    !!conversationId &&
    meta.moldySuppressBranchPicker !== true &&
    hasBranchNumbers &&
    (meta.moldyBranchPickerDisplayOnly === true || siblingCheckpoints.length === branchTotal)
  )
}

function BranchPicker() {
  const t = useTranslations('chat.branch')
  const conversationId = useChatConversationId()
  const queryClient = useQueryClient()
  const [pendingCheckpointId, setPendingCheckpointId] = useState<string | null>(null)
  const threadIsRunning = useAuiState((s) => s.thread.isRunning)
  const pendingEditSuppression = useAtomValue(pendingEditBranchPickerSuppressionAtom)
  const messageId = useAuiState((s) => (typeof s.message?.id === 'string' ? s.message.id : null))
  const messageText = useAuiState((s) => getMessageCopyText(s.message?.content))
  const meta = useAuiState(
    (s) =>
      ((s.message?.metadata as { custom?: BranchMeta } | undefined)?.custom ?? {}) as BranchMeta,
  )
  const siblingCheckpoints = useMemo(
    () => meta.siblingCheckpointIds ?? [],
    [meta.siblingCheckpointIds],
  )
  const displayOnly = meta.moldyBranchPickerDisplayOnly === true

  const switchTo = useCallback(
    async (targetIdx: number) => {
      if (!conversationId || pendingCheckpointId || threadIsRunning || displayOnly) return
      const checkpointId = siblingCheckpoints[targetIdx]
      if (!checkpointId) {
        reportClientWarning('BranchPicker', 'missing checkpoint id for sibling idx', targetIdx)
        return
      }
      setPendingCheckpointId(checkpointId)
      try {
        await conversationsApi.switchBranch(conversationId, checkpointId)
        await queryClient.refetchQueries({
          queryKey: conversationKeys.messages(conversationId),
          type: 'active',
        })
        dispatchMoldyBranchSwitched({ conversationId, checkpointId })
      } catch (err) {
        reportClientError('BranchPicker', 'switch failed', err)
      } finally {
        setPendingCheckpointId(null)
      }
    },
    [
      conversationId,
      displayOnly,
      pendingCheckpointId,
      queryClient,
      siblingCheckpoints,
      threadIsRunning,
    ],
  )

  // M3 — branch picker 억제는 편집 중인 그 메시지에만 적용한다. messageId가
  // 있으면 id로만 매칭하고, id가 없을 때만 content fallback을 쓴다. 예전처럼
  // id || content로 OR 매칭하면 본문이 같은 다른 메시지("응", "ok" 등)의
  // branch picker까지 잘못 숨겨졌다.
  const pendingEditMatchesMessage =
    pendingEditSuppression?.conversationId === conversationId &&
    (messageId != null
      ? pendingEditSuppression.messageId === messageId
      : pendingEditSuppression.content === messageText)
  const branchIsNewest =
    typeof meta.branchTotal !== 'number' ||
    meta.branchTotal < 2 ||
    meta.branchIndex === meta.branchTotal - 1
  if (pendingEditMatchesMessage && !branchIsNewest) return null
  if (!canRenderBranchPicker(conversationId, meta)) return null
  // canRenderBranchPicker guarantees conversationId is non-null + the index/
  // total/checkpoint counts line up. The casts below are narrowing helpers
  // because the type guard only narrows ``meta``.
  const branchTotal = meta.branchTotal as number
  const currentIdx = meta.branchIndex as number

  const total = branchTotal
  const display = currentIdx + 1
  const isSwitching = pendingCheckpointId !== null
  const controlsDisabled = isSwitching || threadIsRunning || displayOnly
  return (
    <span
      className="inline-flex items-center gap-0.5 moldy-ui-micro tabular-nums text-muted-foreground"
      data-moldy-branch-picker="true"
    >
      <button
        type="button"
        className="inline-flex size-4 items-center justify-center rounded hover:bg-accent disabled:opacity-30"
        disabled={controlsDisabled || currentIdx <= 0}
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
        disabled={controlsDisabled || currentIdx >= total - 1}
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
  const submitted = useAuiState(
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
  const { canCancel, canSend, cancel } = useMessageEditComposerControls()
  return (
    <MessageEditComposerRoot className="moldy-chat-card flex flex-col gap-2 p-2">
      <MessageEditComposerInput
        className="min-h-10 w-full resize-none bg-transparent px-2 py-1 text-sm leading-relaxed outline-hidden"
        autoFocus
      />
      <div className="flex items-center justify-end gap-1">
        <Button type="button" size="sm" variant="ghost" disabled={!canCancel} onClick={cancel}>
          {t('editCancel')}
        </Button>
        <Button type="submit" size="sm" disabled={!canSend}>
          {t('editSave')}
        </Button>
      </div>
    </MessageEditComposerRoot>
  )
}

export interface AssistantThreadProps {
  agentImageUrl?: string | null
  /** true이면 agentImageUrl을 frontend public 자산으로 처리 (API_BASE prepend X) */
  agentImagePublicAsset?: boolean
  agentName?: string
  user?: User | null
  /** 토큰 바 표시에 사용할 모델명 */
  modelName?: string
  /** true이면 Composer 토큰 바 표시 */
  showTokenBar?: boolean
  /** true이면 Composer 하단에 컨텍스트 창 사용량 게이지 표시(메인 v3 채팅 전용).
   * 켜지면 모델명은 상단 바 대신 게이지 옆(하단)에 표시된다. */
  showContextGauge?: boolean
  /** 컨텍스트 게이지 한도. agent.model.context_window. null이면 게이지 비활성. */
  contextWindow?: number | null
  /** 컴팩트 모드 (AssistantPanel용) — Composer 높이 축소 */
  compact?: boolean
  /** true이면 메시지 하단에 createdAt 시간 라벨 표시 */
  showMessageTimestamp?: boolean
  /** 빈 상태 커스텀 */
  emptyContent?: React.ReactNode
  /** 추가 도구 UI */
  toolUI?: readonly AssistantToolUI[]
  dataUI?: readonly AssistantDataUI[]
  activities?: readonly RunActivity[]
  deepAgentsState?: DeepAgentsStateSnapshot
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

interface AssistantThreadDynamicContextValue {
  readonly activities: readonly RunActivity[]
  readonly agentImagePublicAsset: boolean
  readonly agentImageUrl?: string | null
  readonly agentName?: string
  readonly builderAgentSubtitle?: string
  readonly deepAgentsState?: DeepAgentsStateSnapshot
  readonly isBuilder: boolean
  readonly showMessageTimestamp: boolean
  readonly user?: User | null
}

const AssistantThreadDynamicContext = createContext<AssistantThreadDynamicContextValue | null>(null)

function useAssistantThreadDynamicContext(): AssistantThreadDynamicContextValue {
  const value = useContext(AssistantThreadDynamicContext)
  if (!value) {
    throw new Error('AssistantThreadDynamicContext is missing')
  }
  return value
}

export function AssistantThread({
  agentImageUrl,
  agentImagePublicAsset = false,
  agentName,
  user,
  modelName,
  showTokenBar = false,
  showContextGauge = false,
  contextWindow,
  compact = false,
  showMessageTimestamp = false,
  emptyContent,
  toolUI,
  dataUI,
  activities = [],
  deepAgentsState,
  enableAttachments = false,
  conversationId,
  variant = 'default',
  builderModelLabel,
  builderAgentSubtitle,
}: AssistantThreadProps) {
  const tPage = useTranslations('chat.page')
  const isBuilder = variant === 'builder'
  const [isViewportAtBottom, setIsViewportAtBottom] = useState(true)
  const handleViewportScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    const nextIsAtBottom = isThreadViewportAtBottom(event.currentTarget)
    setIsViewportAtBottom((current) => (current === nextIsAtBottom ? current : nextIsAtBottom))
  }, [])

  const dynamicContextValue = useMemo(
    () => ({
      activities,
      agentImagePublicAsset,
      agentImageUrl,
      agentName,
      builderAgentSubtitle,
      deepAgentsState,
      isBuilder,
      showMessageTimestamp,
      user,
    }),
    [
      activities,
      agentImagePublicAsset,
      agentImageUrl,
      agentName,
      builderAgentSubtitle,
      deepAgentsState,
      isBuilder,
      showMessageTimestamp,
      user,
    ],
  )

  const messageComponents = useMemo(
    () => ({
      UserMessage: function UserMsg() {
        const { isBuilder, showMessageTimestamp, user } = useAssistantThreadDynamicContext()
        const messageId = useAuiState((s) => s.message?.id)
        const metaRow = (
          <MessageMetaRow>
            <BranchPicker />
            <EditButton />
            <CopyButton />
            {showMessageTimestamp && <MessageTimestamp />}
          </MessageMetaRow>
        )
        if (isBuilder) {
          return (
            <Suspense fallback={<BuilderMessageFallback />}>
              <BuilderUserMessage metaRow={metaRow} />
            </Suspense>
          )
        }
        return (
          // M2 — off-screen `content-visibility:auto`/`contain-intrinsic-size`
          // 최적화는 `fix(chat): stabilize branch rendering`에서 의도적으로
          // 제거했다. off-screen 메시지의 렌더를 건너뛰면 message-id scroll
          // targeting, `<n/m>` branch picker, meta-row `:has()` 가시성 등
          // 레이아웃/측정 의존 기능이 어긋났다. 공용 `.moldy-content-visibility`
          // 는 `contain-intrinsic-size`가 1px 680px로 고정이라 메시지 높이에도
          // 맞지 않으므로 대안이 아니다. 복원 금지.
          <div
            className="group relative flex justify-end gap-3"
            data-moldy-message-id={messageId}
            data-moldy-message-role="user"
          >
            <div className="flex w-full max-w-[80%] flex-col items-end">
              <div className="moldy-chat-bubble-user px-4 py-2.5 text-sm leading-relaxed">
                <MessagePrimitive.Content />
              </div>
              {/* 보낸 첨부를 버블 아래 인라인 렌더 — 클릭 시 미리보기 다이얼로그 */}
              <UserMessageAttachments />
              {metaRow}
            </div>
            <UserAvatar user={user} size="sm" />
          </div>
        )
      },
      UserEditComposer: function UserEdit() {
        const { isBuilder, user } = useAssistantThreadDynamicContext()
        if (isBuilder) {
          return (
            <Suspense fallback={<BuilderMessageFallback />}>
              <BuilderUserEditComposer />
            </Suspense>
          )
        }
        return (
          <div className="flex justify-end gap-3">
            <div className="flex w-full max-w-[80%] flex-col items-end">
              <UserMessageEditor />
            </div>
            <UserAvatar user={user} size="sm" />
          </div>
        )
      },
      AssistantMessage: function AssistantMsg() {
        const {
          activities,
          agentImagePublicAsset,
          agentImageUrl,
          agentName,
          builderAgentSubtitle,
          deepAgentsState,
          isBuilder,
          showMessageTimestamp,
        } = useAssistantThreadDynamicContext()
        const messageId = useAuiState((s) => s.message?.id)
        const tChat = useTranslations('chat')
        const terminalNotice = useAuiState(
          (s) =>
            (
              s.message?.metadata as
                | { custom?: { terminalNotice?: TerminalNoticeStatus } }
                | undefined
            )?.custom?.terminalNotice,
        )
        const isFailedNotice = terminalNotice === 'failed'
        const metaRow = isFailedNotice ? null : (
          <MessageMetaRow>
            <BranchPicker />
            <CopyButton />
            <RegenerateButton />
            <FeedbackButtons />
            <TokenUsagePopover />
            {showMessageTimestamp && <MessageTimestamp />}
          </MessageMetaRow>
        )
        if (isBuilder) {
          // NOTE(G2): 빌더 대화는 에러 retry 스코프 밖이다. failed notice에
          // 도달해도 danger 에러 버블/RetryButton 없이 일반 빌더 메시지로
          // 렌더되고 metaRow는 null이다(아래 isFailedNotice 버블은 non-builder 전용).
          return (
            <Suspense fallback={<BuilderMessageFallback />}>
              <BuilderAssistantMessage metaRow={metaRow} agentSubtitle={builderAgentSubtitle}>
                <StreamingMessageLoadingIndicator
                  activities={activities}
                  deepAgentsState={deepAgentsState}
                />
                <BuilderAssistantMessageParts />
              </BuilderAssistantMessage>
            </Suspense>
          )
        }
        return (
          // M2 — UserMessage와 동일하게 off-screen content-visibility 최적화를
          // 의도적으로 제거한 상태다(`fix(chat): stabilize branch rendering`).
          // 복원 금지 — 위 UserMessage 주석 참고.
          <div
            className="group relative flex gap-3"
            data-moldy-message-id={messageId}
            data-moldy-message-role="assistant"
          >
            <StreamingMessageLoadingIndicator
              activities={activities}
              deepAgentsState={deepAgentsState}
              className="absolute -top-5 left-11 mb-0"
            />
            <AgentAvatar
              imageUrl={agentImageUrl ?? null}
              name={agentName ?? tChat('defaultAgentName')}
              size="sm"
              publicAsset={agentImagePublicAsset}
            />
            <div className="min-w-0 flex-1">
              {isFailedNotice ? (
                <div className="moldy-status-surface moldy-status-danger flex items-start gap-2 rounded-lg px-3 py-2.5 leading-normal">
                  <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
                  <div className="min-w-0 flex-1 space-y-2">
                    <AssistantMessageParts />
                    <RetryButton />
                  </div>
                </div>
              ) : (
                <>
                  <AssistantMessageParts />
                  <AssistantArtifactCards />
                  <AssistantCompactionMarker />
                </>
              )}
              {metaRow}
            </div>
          </div>
        )
      },
    }),
    [],
  )

  return (
    <AssistantThreadDynamicContext.Provider value={dynamicContextValue}>
      <ChatConversationContext.Provider value={conversationId ?? null}>
        <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col">
          <ThreadPrimitive.Viewport
            className="min-h-0 flex-1 overflow-y-auto"
            onScroll={handleViewportScroll}
          >
            <AuiIf condition={(s) => s.thread.isEmpty}>
              {emptyContent ?? (
                <div className="flex h-full items-center justify-center py-8 text-center text-muted-foreground">
                  <p className="text-sm">{tPage('emptyState')}</p>
                </div>
              )}
            </AuiIf>

            <div
              className={cn(
                'mx-auto w-full px-4 py-4',
                isBuilder ? 'max-w-4xl space-y-6' : 'max-w-3xl space-y-4',
              )}
            >
              <ThreadPrimitive.Messages>
                {({ message }) => {
                  const Component = message.composer.isEditing
                    ? messageComponents.UserEditComposer
                    : message.role === 'user'
                      ? messageComponents.UserMessage
                      : messageComponents.AssistantMessage
                  return <Component />
                }}
              </ThreadPrimitive.Messages>
            </div>

            <ThreadPrimitive.ViewportFooter className="pointer-events-none sticky bottom-0 z-10 flex justify-center pb-2">
              <ScrollToBottomButton isAtBottom={isViewportAtBottom} />
            </ThreadPrimitive.ViewportFooter>
          </ThreadPrimitive.Viewport>

          {toolUI?.map((ToolComponent, i) => (
            <ToolComponent key={`tool-${i}`} />
          ))}
          {dataUI?.map((DataComponent, i) => (
            <DataComponent key={`data-${i}`} />
          ))}

          <ReconnectIndicator />

          {/* Composer */}
          {isBuilder ? (
            <Suspense fallback={<BuilderComposerFallback />}>
              <BuilderComposer modelLabel={builderModelLabel} />
            </Suspense>
          ) : (
            <div className="mx-auto w-full max-w-3xl px-4 pb-4">
              <ThreadComposer
                modelName={modelName}
                showTokenBar={showTokenBar}
                showContextGauge={showContextGauge}
                contextWindow={contextWindow}
                compact={compact}
                enableAttachments={enableAttachments}
                focusKey={conversationId}
              />
            </div>
          )}
        </ThreadPrimitive.Root>
      </ChatConversationContext.Provider>
    </AssistantThreadDynamicContext.Provider>
  )
}

function ScrollToBottomButton({ isAtBottom }: { isAtBottom: boolean }) {
  const t = useTranslations('chat.input')
  const scrollToBottom = useThreadViewport((v) => v.scrollToBottom)

  return (
    <button
      type="button"
      aria-label={t('scrollToBottom')}
      aria-hidden={isAtBottom}
      disabled={isAtBottom}
      tabIndex={isAtBottom ? -1 : 0}
      className={cn(
        'moldy-floating-icon-button flex size-8 items-center justify-center text-muted-foreground',
        isAtBottom ? 'pointer-events-none opacity-0' : 'pointer-events-auto opacity-100',
      )}
      onClick={() => scrollToBottom()}
    >
      <ArrowDownIcon className="size-4" />
    </button>
  )
}

function ThreadComposer({
  modelName,
  showTokenBar,
  showContextGauge = false,
  contextWindow,
  compact,
  enableAttachments = false,
  focusKey,
}: {
  modelName?: string
  showTokenBar?: boolean
  showContextGauge?: boolean
  contextWindow?: number | null
  compact?: boolean
  enableAttachments?: boolean
  focusKey?: string | null
}) {
  const t = useTranslations('chat.input')
  const tMsg = useTranslations('chat.message')
  const tFiles = useTranslations('chat.files')
  const conversationId = useChatConversationId()
  const setRightRail = useSetAtom(chatRightRailAtom)
  // Run-complete → refetch /files so a just-sent attachment shows inline
  // without waiting for staleTime or a reload (message_id backfills at finalize).
  useInvalidateFilesOnRunComplete(conversationId)
  const openFilesPanel = () => {
    if (!conversationId) return
    setRightRail({ mode: 'artifacts', artifacts: { conversationId, view: 'list' } })
  }
  const tokenUsage = useAtomValue(sessionTokenUsageAtom)
  const latestTurnUsage = useAtomValue(latestTurnUsageAtom)
  const hasTokens = showTokenBar && (tokenUsage.inputTokens > 0 || tokenUsage.outputTokens > 0)
  const hasCost = showTokenBar && tokenUsage.cost > 0
  // 컨텍스트 게이지 모드: 모델명·게이지·세션비용을 모두 하단 툴바로 모으고 상단
  // 민트 바는 없앤다(클로드코드式). 레거시 표면은 상단 모델/토큰 바를 그대로 쓴다.
  const showTopModelName = Boolean(modelName) && !showContextGauge
  const topBarVisible = !showContextGauge && (showTopModelName || hasTokens)

  return (
    <ComposerPrimitive.Root className="moldy-chat-card @container">
      {/* Model & Token bar (레거시 표면 전용 — 게이지 모드에선 하단으로 이동) */}
      {topBarVisible && (
        <div className="flex items-center gap-3 border-b border-border/60 bg-primary/35 px-3.5 py-1.5 text-xs text-muted-foreground">
          {showTopModelName && <span className="font-medium text-foreground/70">{modelName}</span>}
          {hasTokens && (
            <TokenBar tokenUsage={tokenUsage} showDivider={false} className="ml-auto" />
          )}
        </div>
      )}

      {/* Attachment preview row (P1-7) — only renders when at least one
          attachment is staged; hidden otherwise so the composer stays compact. */}
      {enableAttachments && (
        <ComposerPrimitive.Attachments>{() => <AttachmentChip />}</ComposerPrimitive.Attachments>
      )}

      {/* Textarea */}
      <ImeSafeComposerInput
        autoFocus
        autoFocusKey={focusKey}
        placeholder={t('placeholder')}
        submitMode="enter"
        className={cn(
          'w-full resize-none bg-transparent px-3.5 py-2.5 text-sm leading-relaxed outline-hidden',
          'placeholder:text-muted-foreground',
          'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
          compact ? 'min-h-10 max-h-32' : 'min-h-11 max-h-40',
        )}
        rows={1}
      />

      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 px-2 py-1.5">
        <div className="flex min-w-0 items-center gap-1">
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
          {/* 대화의 파일(생성+첨부) 목록 패널 열기 — 첨부만 있는 대화도 도달 가능. */}
          {conversationId && (
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              className="text-muted-foreground"
              aria-label={tFiles('openPanel')}
              title={tFiles('openPanel')}
              onClick={openFilesPanel}
            >
              <FolderOpenIcon className="size-4" />
            </Button>
          )}
        </div>
        {/* 오른쪽 아래: 모델명 + 컨텍스트 게이지 + 세션 총비용(클로드코드式) + Send/Stop */}
        <div className="flex min-w-0 items-center gap-1.5">
          {showContextGauge && (
            <ContextWindowGauge
              usage={latestTurnUsage}
              contextWindow={contextWindow}
              modelName={modelName}
            />
          )}
          {showContextGauge && hasCost && (
            <span
              className="flex shrink-0 items-center gap-1 moldy-ui-micro tabular-nums text-muted-foreground"
              title={t('sessionCost')}
            >
              <CoinsIcon className="size-3" aria-hidden />
              {formatCost(tokenUsage.cost)}
            </span>
          )}
          <AuiIf condition={(s) => !s.thread.isRunning}>
            <ComposerPrimitive.Send asChild>
              <Button type="submit" size="icon-sm" className="rounded-full">
                <SendIcon className="size-4" />
                <span className="sr-only">{t('sendButton')}</span>
              </Button>
            </ComposerPrimitive.Send>
          </AuiIf>
          <AuiIf condition={(s) => s.thread.isRunning}>
            <StopButton />
          </AuiIf>
        </div>
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
  const isCanceling = useAtomValue(chatCancelInFlightAtom)
  const handleStop = () => {
    if (isCanceling) return
    try {
      aui.thread().cancelRun()
    } catch (err) {
      reportClientWarning('StopButton', 'cancelRun error:', err)
    }
  }
  return (
    <button
      type="button"
      onClick={handleStop}
      disabled={isCanceling}
      aria-label={tMsg('stop')}
      data-moldy-stop-button="true"
      className="inline-flex h-8 items-center gap-1.5 rounded-[9px] border border-input bg-background px-3 moldy-ui-compact font-medium text-foreground/80 transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-50"
    >
      <span aria-hidden className="block size-2.5 rounded-sm bg-foreground/80" />
      {tMsg('stop')}
    </button>
  )
}

/** P1-7 — preview chip for one staged attachment. Renders inside the
 * ``ComposerPrimitive.Attachments`` slot which already provides per-attachment
 * context, so we just read from ``useAuiState(s.attachment)``. */
function AttachmentChip() {
  const tMsg = useTranslations('chat.message')
  const attachment = useAuiState(
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
      <span className="max-w-48 truncate">
        <AttachmentPrimitive.Name />
      </span>
      {isUploading && (
        <span className="moldy-ui-micro text-muted-foreground">{tMsg('attachmentUploading')}</span>
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
  return formatCompactCount(n, { thousandSuffix: 'k' })
}

function formatCost(n: number): string {
  const decimals = n < 0.01 ? 4 : 2
  return formatDisplayUsd(n, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  })
}
