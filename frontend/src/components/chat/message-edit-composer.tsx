'use client'

import {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  type ClipboardEvent,
  type ForwardedRef,
  type KeyboardEvent,
  type TextareaHTMLAttributes,
  type FormHTMLAttributes,
} from 'react'
import { useAui, useAuiState } from '@assistant-ui/react'
import { useSetAtom } from 'jotai'

import { reportClientError } from '@/lib/logging/client-logger'
import { cn } from '@/lib/utils'
import { pendingEditBranchPickerSuppressionAtom } from '@/lib/stores/chat-store'
import { requestThreadComposerFocus } from './composer-focus'
import { useChatConversationId } from './conversation-context'

type SubmitMode = 'enter' | 'ctrlEnter' | 'none'
type MessageEditComposerRootProps = FormHTMLAttributes<HTMLFormElement>
type MessageEditComposerSubmitEvent = Parameters<
  NonNullable<MessageEditComposerRootProps['onSubmit']>
>[0]

type MessageEditComposerInputProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value'> & {
  submitMode?: SubmitMode
  submitOnEnter?: boolean
  addAttachmentOnPaste?: boolean
}

function assignRef<T>(ref: ForwardedRef<T>, value: T | null): void {
  if (typeof ref === 'function') {
    ref(value)
    return
  }
  if (ref) {
    ref.current = value
  }
}

export function MessageEditComposerRoot({
  children,
  onSubmit,
  ...props
}: MessageEditComposerRootProps) {
  const aui = useAui()
  const conversationId = useChatConversationId()
  const setPendingBranchPickerSuppression = useSetAtom(pendingEditBranchPickerSuppressionAtom)

  function handleSubmit(event: MessageEditComposerSubmitEvent) {
    onSubmit?.(event)
    if (event.defaultPrevented) return

    event.preventDefault()
    const composer = aui.message().composer()
    const state = composer.getState()
    if (!state.isEditing || state.isEmpty) return
    const message = aui.message().getState()
    setPendingBranchPickerSuppression({
      conversationId,
      messageId: typeof message.id === 'string' ? message.id : null,
      content: state.text,
    })
    composer.send()
    requestThreadComposerFocus()
  }

  return (
    <form {...props} onSubmit={handleSubmit}>
      {children}
    </form>
  )
}

export function useMessageEditComposerControls() {
  const aui = useAui()
  const setPendingBranchPickerSuppression = useSetAtom(pendingEditBranchPickerSuppressionAtom)
  const canCancel = useAuiState((state) => state.message.composer.canCancel)
  const canSend = useAuiState(
    (state) =>
      state.message.composer.isEditing &&
      !state.message.composer.isEmpty &&
      !state.thread.isDisabled,
  )

  const cancel = useCallback(() => {
    const composer = aui.message().composer()
    if (composer.getState().canCancel) {
      composer.cancel()
      setPendingBranchPickerSuppression(null)
      requestThreadComposerFocus()
    }
  }, [aui, setPendingBranchPickerSuppression])

  return { canCancel, canSend, cancel }
}

export const MessageEditComposerInput = forwardRef<
  HTMLTextAreaElement,
  MessageEditComposerInputProps
>(
  (
    {
      autoFocus = false,
      className,
      disabled,
      onChange,
      onCompositionEnd,
      onCompositionStart,
      onKeyDown,
      onPaste,
      submitMode,
      submitOnEnter,
      addAttachmentOnPaste = true,
      ...props
    },
    forwardedRef,
  ) => {
    const aui = useAui()
    const conversationId = useChatConversationId()
    const setPendingBranchPickerSuppression = useSetAtom(pendingEditBranchPickerSuppressionAtom)
    const textareaRef = useRef<HTMLTextAreaElement | null>(null)
    const compositionRef = useRef(false)
    const effectiveSubmitMode = submitMode ?? (submitOnEnter === false ? 'none' : 'enter')

    const externalValue = useAuiState((state) =>
      state.message.composer.isEditing ? state.message.composer.text : '',
    )
    const runtimeDisabled = useAuiState(
      (state) => state.thread.isDisabled || state.message.composer.dictation?.inputDisabled,
    )
    const isDisabled = Boolean(disabled || runtimeDisabled)

    const setTextareaRef = useCallback(
      (node: HTMLTextAreaElement | null) => {
        textareaRef.current = node
        assignRef(forwardedRef, node)
      },
      [forwardedRef],
    )

    useEffect(() => {
      if (!autoFocus || isDisabled) return
      const textarea = textareaRef.current
      if (!textarea) return
      textarea.focus({ preventScroll: true })
      textarea.setSelectionRange(textarea.value.length, textarea.value.length)
    }, [autoFocus, isDisabled])

    useEffect(() => {
      if (compositionRef.current) return
      const textarea = textareaRef.current
      if (!textarea || textarea.value === externalValue) return
      textarea.value = externalValue
    }, [externalValue])

    const syncText = useCallback(
      (next: string) => {
        const composer = aui.message().composer()
        if (!composer.getState().isEditing) return
        composer.setText(next)
        const message = aui.message().getState()
        setPendingBranchPickerSuppression({
          conversationId,
          messageId: typeof message.id === 'string' ? message.id : null,
          content: next,
        })
      },
      [aui, conversationId, setPendingBranchPickerSuppression],
    )

    const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
      onKeyDown?.(event)
      if (event.defaultPrevented || isDisabled) return
      if (event.nativeEvent.isComposing || compositionRef.current) return
      if (event.key !== 'Enter') return

      const threadState = aui.thread().getState()
      if (event.shiftKey) return
      if (threadState.isRunning && !threadState.capabilities.queue) return

      const shouldSubmit =
        effectiveSubmitMode === 'enter' ||
        (effectiveSubmitMode === 'ctrlEnter' && (event.ctrlKey || event.metaKey))

      if (shouldSubmit) {
        event.preventDefault()
        textareaRef.current?.closest('form')?.requestSubmit()
      }
    }

    const handlePaste = async (event: ClipboardEvent<HTMLTextAreaElement>) => {
      onPaste?.(event)
      if (event.defaultPrevented || !addAttachmentOnPaste) return

      const files = Array.from(event.clipboardData?.files || [])
      if (!files.length || !aui.thread().getState().capabilities.attachments) return

      try {
        event.preventDefault()
        const composer = aui.message().composer()
        await Promise.all(files.map((file) => composer.addAttachment(file)))
      } catch (error) {
        reportClientError('MessageEditComposerInput', 'add attachment error:', error)
      }
    }

    return (
      <textarea
        {...props}
        ref={setTextareaRef}
        defaultValue={externalValue}
        disabled={isDisabled}
        className={cn('field-sizing-content', className)}
        onChange={(event) => {
          onChange?.(event)
          if (event.defaultPrevented) return
          if (compositionRef.current) return
          syncText(event.currentTarget.value)
        }}
        onCompositionStart={(event) => {
          onCompositionStart?.(event)
          compositionRef.current = true
        }}
        onCompositionEnd={(event) => {
          onCompositionEnd?.(event)
          compositionRef.current = false
          syncText(event.currentTarget.value)
        }}
        onKeyDown={handleKeyDown}
        onPaste={(event) => {
          void handlePaste(event)
        }}
      />
    )
  },
)

MessageEditComposerInput.displayName = 'MessageEditComposerInput'
