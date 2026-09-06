'use client'

import { useRef, useState } from 'react'
import { AuiIf, ComposerPrimitive, useAui } from '@assistant-ui/react'
import { CoinsIcon, FolderOpenIcon, PaperclipIcon, SendIcon, WandSparklesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useAtom, useAtomValue, useSetAtom } from 'jotai'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { ContextWindowGauge } from '@/components/chat/context-window-gauge'
import { ImeSafeComposerInput } from '@/components/chat/ime-safe-composer-input'
import { ComposerGhostSuggestion } from '@/components/chat/composer-ghost-suggestion'
import { useComposerHistory } from '@/components/chat/use-composer-history'
import { useFollowupGhost } from '@/components/chat/use-followup-ghost'
import { useFollowupSuggestion } from '@/components/chat/use-followup-suggestion'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { useInvalidateFilesOnRunComplete } from '@/components/chat/use-files-run-sync'
import { formatComposerCost, TokenBar } from '@/components/chat/assistant-composer-usage'
import { AttachmentChip } from '@/components/chat/assistant-composer-attachment'
import { ComposerDictationControl } from '@/components/chat/composer-dictation-control'
import type { DictationAvailability } from '@/components/chat/use-browser-dictation'
import { followupEnabledAtom } from '@/lib/stores/chat-followup'
import {
  chatCancelInFlightAtom,
  latestTurnUsageAtom,
  sessionTokenUsageAtom,
} from '@/lib/stores/chat-store'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { reportClientWarning } from '@/lib/logging/client-logger'

export interface ThreadComposerProps {
  readonly modelName?: string
  readonly showTokenBar?: boolean
  readonly showContextGauge?: boolean
  readonly contextWindow?: number | null
  readonly compact?: boolean
  readonly enableAttachments?: boolean
  readonly focusKey?: string | null
  readonly dictationAvailability?: DictationAvailability
  readonly onDictationStart?: () => void
}

export function ThreadComposer({
  modelName,
  showTokenBar,
  showContextGauge = false,
  contextWindow,
  compact,
  enableAttachments = false,
  focusKey,
  dictationAvailability = 'unsupported',
  onDictationStart = () => {},
}: ThreadComposerProps) {
  const t = useTranslations('chat.input')
  const tMsg = useTranslations('chat.message')
  const tFiles = useTranslations('chat.files')
  const tFollowup = useTranslations('chat.followup')
  const conversationId = useChatConversationId()
  const setRightRail = useSetAtom(chatRightRailAtom)
  useInvalidateFilesOnRunComplete(conversationId)
  const composerTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  useFollowupSuggestion(conversationId)
  const [followupEnabled, setFollowupEnabled] = useAtom(followupEnabledAtom)
  const { ghostText, handleGhostKeyDown, acceptGhost } = useFollowupGhost(
    conversationId,
    composerTextareaRef,
  )
  const { handleHistoryKeyDown } = useComposerHistory(conversationId)
  const [composing, setComposing] = useState(false)
  const ghostVisible = Boolean(ghostText) && !composing
  const openFilesPanel = () => {
    if (!conversationId) return
    setRightRail({ mode: 'artifacts', artifacts: { conversationId, view: 'list' } })
  }
  const tokenUsage = useAtomValue(sessionTokenUsageAtom)
  const latestTurnUsage = useAtomValue(latestTurnUsageAtom)
  const hasTokens = showTokenBar && (tokenUsage.inputTokens > 0 || tokenUsage.outputTokens > 0)
  const hasCost = showTokenBar && tokenUsage.cost > 0
  const showTopModelName = Boolean(modelName) && !showContextGauge
  const topBarVisible = !showContextGauge && (showTopModelName || hasTokens)

  return (
    <ComposerPrimitive.Root className="moldy-chat-card @container">
      {topBarVisible && (
        <div className="flex items-center gap-3 border-b border-border/60 bg-primary/35 px-3.5 py-1.5 text-xs text-muted-foreground">
          {showTopModelName && <span className="font-medium text-foreground/70">{modelName}</span>}
          {hasTokens && (
            <TokenBar tokenUsage={tokenUsage} showDivider={false} className="ml-auto" />
          )}
        </div>
      )}
      {enableAttachments && (
        <ComposerPrimitive.Attachments>{() => <AttachmentChip />}</ComposerPrimitive.Attachments>
      )}
      <div className="relative">
        <ImeSafeComposerInput
          ref={composerTextareaRef}
          autoFocus
          autoFocusKey={focusKey}
          placeholder={ghostVisible ? '' : t('placeholder')}
          submitMode="enter"
          onKeyDown={(event) => {
            handleGhostKeyDown(event)
            if (!event.defaultPrevented) handleHistoryKeyDown(event)
          }}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={() => setComposing(false)}
          className={cn(
            'w-full resize-none bg-transparent px-3.5 py-2.5 text-sm leading-relaxed outline-hidden',
            'placeholder:text-muted-foreground',
            'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
            compact ? 'min-h-10 max-h-32' : 'min-h-11 max-h-40',
          )}
          rows={1}
        />
        {ghostVisible && ghostText ? (
          <ComposerGhostSuggestion text={ghostText} onAccept={acceptGhost} />
        ) : null}
      </div>
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
          <ComposerDictationControl
            availability={dictationAvailability}
            focusKey={focusKey}
            onStart={onDictationStart}
          />
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
          {conversationId && (
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-pressed={followupEnabled}
              data-moldy-followup-toggle={followupEnabled ? 'on' : 'off'}
              className={followupEnabled ? 'text-primary-strong' : 'text-muted-foreground'}
              aria-label={followupEnabled ? tFollowup('toggleOff') : tFollowup('toggleOn')}
              title={followupEnabled ? tFollowup('toggleOff') : tFollowup('toggleOn')}
              onClick={() => setFollowupEnabled((value) => !value)}
            >
              <WandSparklesIcon className="size-4" />
            </Button>
          )}
        </div>
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
              {formatComposerCost(tokenUsage.cost)}
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

function StopButton() {
  const aui = useAui()
  const tMsg = useTranslations('chat.message')
  const isCanceling = useAtomValue(chatCancelInFlightAtom)
  const handleStop = () => {
    if (isCanceling) return
    try {
      aui.thread.cancelRun()
    } catch (error) {
      reportClientWarning('StopButton', 'cancelRun error:', error)
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
