'use client'

import {
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type ForwardedRef,
  type KeyboardEvent,
  type TextareaHTMLAttributes,
  type FormHTMLAttributes,
} from 'react'
import { useAui, useAuiState, type RunConfig } from '@assistant-ui/react'
import { useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { AgUiChatActionContext } from '@/lib/ag-ui/chat-action-context'
import { canBranchFromChatMessage } from '@/lib/ag-ui/chat-message-identity'
import { reportClientError } from '@/lib/logging/client-logger'
import { cn } from '@/lib/utils'
import { pendingEditBranchPickerSuppressionAtom } from '@/lib/stores/chat-store'
import { requestThreadComposerFocus } from './composer-focus'
import { useChatConversationId } from './conversation-context'

type SubmitMode = 'enter' | 'ctrlEnter' | 'none'
type MessageEditComposerRootProps = FormHTMLAttributes<HTMLFormElement> & {
  canSubmit?: boolean
}
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

export function useBranchActionReady() {
  const actions = useContext(AgUiChatActionContext)
  return useAuiState(
    (s) =>
      !actions ||
      (!s.thread.isRunning && canBranchFromChatMessage(s.message, s.thread.messages.at(-1))),
  )
}

// Resolving a saved checkpoint can outlive the selected message or its conversation.
export function useMessageBranchAction() {
  const actions = useContext(AgUiChatActionContext)
  const aui = useAui()
  const conversationId = useChatConversationId()
  const t = useTranslations('chat.page')
  const pending = useRef<symbol | null>(null)
  const enabled = Boolean(actions)
  useLayoutEffect(
    () => () => {
      pending.current = null
    },
    [aui, conversationId, enabled],
  )

  if (!actions) return null
  return async (
    operation: 'edit' | 'regenerate',
    apply: (config: RunConfig) => void,
    existing?: RunConfig,
  ): Promise<void> => {
    if (pending.current !== null) return
    const thread = aui.thread.getState()
    if (thread.isDisabled || thread.isRunning) return
    const message = aui.message.getState()
    const head = thread.messages.at(-1)
    if (!canBranchFromChatMessage(message, head)) return
    const request = Symbol()
    pending.current = request
    try {
      const config = await actions.getBranchRunConfig(operation, message, existing)
      if (pending.current !== request) return
      const current = aui.thread.getState()
      if (
        current.isDisabled ||
        current.isRunning ||
        current.messages.at(-1)?.id !== head?.id ||
        current.messages.at(-1)?.metadata.custom.moldySelectedCheckpointId !==
          head?.metadata.custom.moldySelectedCheckpointId ||
        aui.message.getState().id !== message.id
      )
        return
      apply(config)
    } catch (error) {
      if (pending.current === request) {
        reportClientError('MessageBranchAction', operation, error)
        toast.error(t('runFailed'))
      }
    } finally {
      if (pending.current === request) pending.current = null
    }
  }
}

export function MessageEditComposerRoot({
  children,
  onSubmit,
  canSubmit = true,
  ...props
}: MessageEditComposerRootProps) {
  const aui = useAui()
  const conversationId = useChatConversationId()
  const runBranchAction = useMessageBranchAction()
  const setPendingBranchPickerSuppression = useSetAtom(pendingEditBranchPickerSuppressionAtom)

  function handleSubmit(event: MessageEditComposerSubmitEvent) {
    onSubmit?.(event)
    if (event.defaultPrevented) return

    event.preventDefault()
    if (!canSubmit) return
    const composer = aui.message.composer()
    const state = composer.getState()
    if (!state.isEditing || state.isEmpty) return
    const message = aui.message.getState()
    const send = () => {
      setPendingBranchPickerSuppression({
        conversationId,
        messageId: typeof message.id === 'string' ? message.id : null,
        content: state.text,
      })
      composer.send()
      requestThreadComposerFocus()
    }
    if (runBranchAction) {
      void runBranchAction(
        'edit',
        (config) => {
          const current = composer.getState()
          if (
            !current.isEditing ||
            current.text !== state.text ||
            current.runConfig !== state.runConfig ||
            current.quote !== state.quote ||
            current.attachments.length !== state.attachments.length ||
            current.attachments.some((item, index) => item.id !== state.attachments[index]?.id)
          )
            return
          composer.setRunConfig(config)
          send()
        },
        state.runConfig,
      )
    } else {
      send()
    }
  }

  return (
    <form {...props} onSubmit={handleSubmit}>
      {children}
    </form>
  )
}

export function useMessageEditComposerControls() {
  const aui = useAui()
  const actions = useContext(AgUiChatActionContext)
  const ready = useBranchActionReady()
  const selectedLeaf = useAuiState((s) =>
    actions ? s.message.metadata.custom.moldySelectedCheckpointId : undefined,
  )
  // Switching branches can retain the same user row and its still-open SDK edit composer.
  const [openedLeaf] = useState(() => selectedLeaf)
  const setPendingBranchPickerSuppression = useSetAtom(pendingEditBranchPickerSuppressionAtom)
  const canCancel = useAuiState((state) => state.message.composer.canCancel)
  const canSend = useAuiState(
    (state) =>
      state.message.composer.isEditing &&
      !state.message.composer.isEmpty &&
      !state.thread.isDisabled &&
      ready &&
      selectedLeaf === openedLeaf,
  )

  const cancel = useCallback(() => {
    const composer = aui.message.composer()
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
        const composer = aui.message.composer()
        if (!composer.getState().isEditing) return
        composer.setText(next)
        const message = aui.message.getState()
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

      const threadState = aui.thread.getState()
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
      if (!files.length || !aui.thread.getState().capabilities.attachments) return

      try {
        event.preventDefault()
        const composer = aui.message.composer()
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
