'use client'

import {
  unstable_useTriggerPopoverAriaProps,
  unstable_useTriggerPopoverRootContextOptional,
  type Unstable_TriggerPopoverAriaProps,
} from '@assistant-ui/react'
import { useCallback } from 'react'
import type { KeyboardEvent } from 'react'

export type ChatComposerTriggerInputBridge = {
  readonly ariaProps: Unstable_TriggerPopoverAriaProps
  readonly handleKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean
  readonly setCursorPosition: (position: number) => void
}

export function useChatComposerTriggerInput(): ChatComposerTriggerInputBridge {
  const root = unstable_useTriggerPopoverRootContextOptional()
  const ariaProps = unstable_useTriggerPopoverAriaProps()
  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>): boolean => {
      if (event.nativeEvent.isComposing || !root) return false
      for (const trigger of root.getTriggers().values()) {
        if (trigger.resource.handleKeyDown(event)) return true
      }
      return false
    },
    [root],
  )
  const setCursorPosition = useCallback(
    (position: number): void => {
      if (!root) return
      for (const trigger of root.getTriggers().values()) {
        trigger.resource.setCursorPosition(position)
      }
    },
    [root],
  )

  return { ariaProps, handleKeyDown, setCursorPosition }
}
