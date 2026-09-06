'use client'

import { useCallback, useState, type ReactNode } from 'react'
import { ActionBarPrimitive, useAuiState } from '@assistant-ui/react'
import {
  CheckIcon,
  CopyIcon,
  PencilIcon,
  RotateCcwIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatRelativeShort } from '@/lib/utils/format-relative-time'
import { copyTextToClipboard, getMessageCopyText } from '@/components/chat/message-copy'
import { reportClientWarning } from '@/lib/logging/client-logger'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { PinConversationSummaryButton } from '@/components/chat/pin-conversation-summary-button'
import {
  MessageEditComposerInput,
  MessageEditComposerRoot,
  useMessageEditComposerControls,
} from '@/components/chat/message-edit-composer'

const MESSAGE_ACTION_CLASS =
  'inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40'

const RETRY_BUTTON_CLASS =
  'inline-flex items-center gap-1.5 self-start rounded-md px-2 py-1 text-xs font-medium underline-offset-2 transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-40'

export function MessageTimestamp() {
  const tCommon = useTranslations('common')
  const createdAt = useAuiState((s) => (s.message as { createdAt?: Date } | undefined)?.createdAt)
  if (!createdAt) return null
  return (
    <span className="ml-1 shrink-0 tabular-nums moldy-ui-micro text-muted-foreground">
      {formatRelativeShort(createdAt, tCommon('yesterday'))}
    </span>
  )
}

export function MessageMetaRow({ children }: { readonly children: ReactNode }) {
  return (
    <div
      className="mt-1 flex min-h-7 max-w-full items-center gap-0.5 overflow-hidden opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
      data-moldy-message-meta-row="true"
    >
      {children}
    </div>
  )
}

export function CopyButton() {
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
    } catch (error) {
      reportClientWarning('CopyButton', 'failed to copy message text', error)
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

export function EditButton() {
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

export function RegenerateButton() {
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

export function PinSummaryButton() {
  const conversationId = useChatConversationId()
  const t = useTranslations('chat.message')
  return (
    <PinConversationSummaryButton
      conversationId={conversationId}
      pinLabel={t('pinSummary')}
      unpinLabel={t('unpinSummary')}
    />
  )
}

export function RetryButton() {
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

export function FeedbackButtons() {
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

export function UserMessageEditor() {
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
