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
} from 'react'
import { useAui, useAuiState } from '@assistant-ui/react'

import { cn } from '@/lib/utils'

type SubmitMode = 'enter' | 'ctrlEnter' | 'none'

type ImeSafeComposerInputProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value'> & {
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

export const ImeSafeComposerInput = forwardRef<HTMLTextAreaElement, ImeSafeComposerInputProps>(
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
    const textareaRef = useRef<HTMLTextAreaElement | null>(null)
    const compositionRef = useRef(false)
    const effectiveSubmitMode = submitMode ?? (submitOnEnter === false ? 'none' : 'enter')

    const externalValue = useAuiState((state) =>
      state.composer.isEditing ? state.composer.text : '',
    )
    const runtimeDisabled = useAuiState(
      (state) => state.thread.isDisabled || state.composer.dictation?.inputDisabled,
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
        if (!aui.composer().getState().isEditing) return
        aui.composer().setText(next)
      },
      [aui],
    )

    const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
      onKeyDown?.(event)
      if (event.defaultPrevented || isDisabled) return
      if (event.nativeEvent.isComposing || compositionRef.current) return
      if (event.key !== 'Enter') return

      const threadState = aui.thread().getState()
      const hasQueue = threadState.capabilities.queue

      if (
        event.shiftKey &&
        (event.ctrlKey || event.metaKey) &&
        hasQueue &&
        effectiveSubmitMode !== 'none' &&
        !aui.composer().getState().isEmpty
      ) {
        event.preventDefault()
        aui.composer().send({ steer: true })
        return
      }

      if (event.shiftKey) return
      if (threadState.isRunning && !hasQueue) return

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
        await Promise.all(files.map((file) => aui.composer().addAttachment(file)))
      } catch (error) {
        console.error('[ImeSafeComposerInput] add attachment error:', error)
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

ImeSafeComposerInput.displayName = 'ImeSafeComposerInput'
