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

import { reportClientError } from '@/lib/logging/client-logger'
import { useChatComposerTriggerInput } from '@/lib/chat/context/use-chat-composer-trigger-input'
import { cn } from '@/lib/utils'
import { autoFocusComposerInput, focusTextareaAtEnd } from './composer-focus'

type SubmitMode = 'enter' | 'ctrlEnter' | 'none'

type ImeSafeComposerInputProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value'> & {
  submitMode?: SubmitMode
  submitOnEnter?: boolean
  addAttachmentOnPaste?: boolean
  autoFocusKey?: string | number | null
  restoreFocusOnTextClear?: boolean
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
      onSelect,
      submitMode,
      submitOnEnter,
      addAttachmentOnPaste = true,
      autoFocusKey,
      restoreFocusOnTextClear = true,
      ...props
    },
    forwardedRef,
  ) => {
    const aui = useAui()
    const triggerInput = useChatComposerTriggerInput()
    const textareaRef = useRef<HTMLTextAreaElement | null>(null)
    const compositionRef = useRef(false)
    const inputTextSyncRef = useRef(false)
    const setTriggerCursorPositionRef = useRef(triggerInput.setCursorPosition)
    const compositionStartTextRef = useRef('')
    const compositionStartSelectionRef = useRef({ end: 0, start: 0 })
    const effectiveSubmitMode = submitMode ?? (submitOnEnter === false ? 'none' : 'enter')

    const externalValue = useAuiState((state) =>
      state.composer.isEditing ? state.composer.text : '',
    )
    const previousExternalValueRef = useRef(externalValue)
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
      setTriggerCursorPositionRef.current = triggerInput.setCursorPosition
    }, [triggerInput.setCursorPosition])

    useEffect(() => {
      if (!autoFocus || isDisabled) return
      const textarea = textareaRef.current
      if (!textarea) return
      autoFocusComposerInput(textarea)
    }, [autoFocus, autoFocusKey, isDisabled])

    useEffect(() => {
      if (compositionRef.current) return
      const textarea = textareaRef.current
      if (!textarea) return
      if (textarea.value !== externalValue) textarea.value = externalValue
      const wasLocalTextSync = inputTextSyncRef.current
      inputTextSyncRef.current = false
      if (wasLocalTextSync) return

      const cursorPosition = externalValue.length
      const timeout = window.setTimeout(() => {
        setTriggerCursorPositionRef.current(cursorPosition)
      }, 0)
      return () => window.clearTimeout(timeout)
    }, [externalValue])

    useEffect(() => {
      const previousExternalValue = previousExternalValueRef.current
      previousExternalValueRef.current = externalValue

      if (!restoreFocusOnTextClear || isDisabled) return
      if (previousExternalValue === '' || externalValue !== '') return

      const focus = () => {
        focusTextareaAtEnd(textareaRef.current)
      }

      if (typeof window.requestAnimationFrame === 'function') {
        window.requestAnimationFrame(focus)
        return
      }

      window.setTimeout(focus, 0)
    }, [externalValue, isDisabled, restoreFocusOnTextClear])

    const syncText = useCallback(
      (next: string) => {
        if (!aui.composer.getState().isEditing) return
        inputTextSyncRef.current = true
        aui.composer.setText(next)
      },
      [aui],
    )

    const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (!isDisabled && !event.nativeEvent.isComposing && !compositionRef.current) {
        if (triggerInput.handleKeyDown(event)) return
      }
      onKeyDown?.(event)
      if (event.defaultPrevented || isDisabled) return
      if (event.nativeEvent.isComposing || compositionRef.current) return
      if (event.key !== 'Enter') return

      const threadState = aui.thread.getState()
      const hasQueue = threadState.capabilities.queue

      if (
        event.shiftKey &&
        (event.ctrlKey || event.metaKey) &&
        hasQueue &&
        effectiveSubmitMode !== 'none' &&
        !aui.composer.getState().isEmpty
      ) {
        event.preventDefault()
        aui.composer.send({ steer: true })
        return
      }

      if (event.shiftKey) return
      if (threadState.isRunning && !hasQueue) return

      const shouldSubmit =
        effectiveSubmitMode === 'enter' ||
        (effectiveSubmitMode === 'ctrlEnter' && (event.ctrlKey || event.metaKey))

      if (shouldSubmit) {
        event.preventDefault()
        if (hasQueue) {
          aui.composer.send({ steer: false })
          return
        }
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
        await Promise.all(files.map((file) => aui.composer.addAttachment(file)))
      } catch (error) {
        reportClientError('ImeSafeComposerInput', 'add attachment error:', error)
      }
    }

    return (
      <textarea
        {...props}
        {...triggerInput.ariaProps}
        data-moldy-composer-input="true"
        ref={setTextareaRef}
        defaultValue={externalValue}
        disabled={isDisabled}
        className={cn('field-sizing-content', className)}
        onChange={(event) => {
          onChange?.(event)
          if (event.defaultPrevented) return
          if (compositionRef.current) return
          syncText(event.currentTarget.value)
          triggerInput.setCursorPosition(event.currentTarget.selectionStart)
        }}
        onCompositionStart={(event) => {
          onCompositionStart?.(event)
          compositionRef.current = true
          compositionStartTextRef.current = event.currentTarget.value
          compositionStartSelectionRef.current = {
            end: event.currentTarget.selectionEnd,
            start: event.currentTarget.selectionStart,
          }
        }}
        onCompositionEnd={(event) => {
          onCompositionEnd?.(event)
          compositionRef.current = false

          const composedText = event.currentTarget.value
          const compositionStartText = compositionStartTextRef.current
          const { end, start } = compositionStartSelectionRef.current
          const runtimeText = aui.composer.getState().text
          const beforeComposition = compositionStartText.slice(0, start)
          const afterComposition = compositionStartText.slice(end)
          const compositionReplacement = composedText.slice(
            beforeComposition.length,
            composedText.length - afterComposition.length,
          )

          // Dictation updates the runtime while an IME owns the textarea. Reconcile only when
          // the original context still surrounds the IME edit; otherwise the DOM is authoritative.
          const canReconcileDictation =
            composedText.startsWith(beforeComposition) &&
            composedText.endsWith(afterComposition) &&
            runtimeText.startsWith(beforeComposition)
          const nextText = canReconcileDictation
            ? `${beforeComposition}${compositionReplacement}${runtimeText.slice(
                compositionStartText.length - afterComposition.length,
              )}`
            : composedText

          syncText(nextText)
          triggerInput.setCursorPosition(event.currentTarget.selectionStart)
        }}
        onKeyDown={handleKeyDown}
        onPaste={(event) => {
          void handlePaste(event)
        }}
        onSelect={(event) => {
          onSelect?.(event)
          if (event.defaultPrevented) return
          triggerInput.setCursorPosition(event.currentTarget.selectionStart)
        }}
      />
    )
  },
)

ImeSafeComposerInput.displayName = 'ImeSafeComposerInput'
