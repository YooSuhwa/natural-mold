'use client'

const THREAD_COMPOSER_INPUT_SELECTOR =
  'textarea[data-moldy-composer-input="true"]:not(:disabled)'

function canAutoFocusComposer(): boolean {
  if (typeof window === 'undefined') return false
  if (typeof window.matchMedia !== 'function') return true
  return !window.matchMedia('(pointer: coarse)').matches
}

export function focusTextareaAtEnd(textarea: HTMLTextAreaElement | null): boolean {
  if (!textarea || textarea.disabled) return false
  textarea.focus({ preventScroll: true })
  textarea.setSelectionRange(textarea.value.length, textarea.value.length)
  return document.activeElement === textarea
}

export function autoFocusComposerInput(textarea: HTMLTextAreaElement | null): boolean {
  if (!canAutoFocusComposer()) return false
  return focusTextareaAtEnd(textarea)
}

export function requestThreadComposerFocus(): void {
  if (typeof window === 'undefined') return

  const focus = () => {
    const inputs = document.querySelectorAll<HTMLTextAreaElement>(
      THREAD_COMPOSER_INPUT_SELECTOR,
    )
    focusTextareaAtEnd(inputs[inputs.length - 1] ?? null)
  }

  if (typeof window.requestAnimationFrame === 'function') {
    window.requestAnimationFrame(focus)
    return
  }

  window.setTimeout(focus, 0)
}
