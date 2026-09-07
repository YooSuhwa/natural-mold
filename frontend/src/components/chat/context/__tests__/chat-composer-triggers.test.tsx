import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import {
  AssistantRuntimeProvider,
  unstable_useComposerInput,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react'
import { describe, expect, it, vi } from 'vitest'

import { ChatComposerTriggers } from '../chat-composer-triggers'
import type { ChatCommandDefinition } from '@/lib/chat/commands/chat-command-types'
import type { ResourceContextReference } from '@/lib/chat/context/resource-context'
import { useChatComposerTriggerInput } from '@/lib/chat/context/use-chat-composer-trigger-input'

const categoryLabels = {
  artifact: 'Artifact',
  conversation: 'Conversation',
  file: 'File',
  skill: 'Skill',
} as const

const EMPTY_MESSAGES: ThreadMessageLike[] = []

function TriggerAwareInput() {
  const composer = unstable_useComposerInput()
  const trigger = useChatComposerTriggerInput()
  return (
    <textarea
      aria-label="Message"
      value={composer.value}
      {...trigger.ariaProps}
      onChange={(event) => {
        composer.setText(event.currentTarget.value)
        if (!('isComposing' in event.nativeEvent && event.nativeEvent.isComposing)) {
          trigger.setCursorPosition(event.currentTarget.selectionStart)
        }
      }}
      onCompositionEnd={(event) => {
        composer.setText(event.currentTarget.value)
        trigger.setCursorPosition(event.currentTarget.selectionStart)
      }}
      onKeyDown={(event) => {
        trigger.handleKeyDown(event)
      }}
    />
  )
}

function Harness({
  commands,
  onResourceSelect = () => {},
}: {
  readonly commands: readonly ChatCommandDefinition[]
  readonly onResourceSelect?: (reference: ResourceContextReference) => void
}) {
  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: EMPTY_MESSAGES,
    isRunning: false,
    onNew: async () => {},
    convertMessage: (message) => message,
  })
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ChatComposerTriggers
        commands={commands}
        commandAriaLabel="Commands"
        resourceCandidates={[
          {
            kind: 'file',
            id: '8f65c7bb-7092-43f4-a215-8e603101d114',
            label: '회의록',
          },
        ]}
        selectedResourceCount={0}
        resourceCategoryLabels={categoryLabels}
        resourceAriaLabel="Resources"
        resourceLimitReason="Limit reached"
        onResourceSelect={onResourceSelect}
      >
        <TriggerAwareInput />
      </ChatComposerTriggers>
    </AssistantRuntimeProvider>
  )
}

const enabledCommand = (execute: () => void): ChatCommandDefinition => ({
  id: 'search',
  label: '/search',
  description: 'Search transcript',
  acceptsArgument: true,
  availability: { kind: 'enabled' },
  execute,
})

describe('ChatComposerTriggers official trigger integration', () => {
  it('filters commands and uses Arrow/Enter to execute the highlighted real action', async () => {
    const execute = vi.fn()
    render(<Harness commands={[enabledCommand(execute)]} />)
    const input = screen.getByRole('textbox', { name: 'Message' })

    fireEvent.change(input, { target: { value: '/sea', selectionStart: 4, selectionEnd: 4 } })
    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible())
    expect(screen.getByRole('option', { name: /search/i })).toBeVisible()

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(execute).toHaveBeenCalledOnce()
  })

  it('closes with Escape and does not execute Enter while Korean IME is composing', async () => {
    const execute = vi.fn()
    render(<Harness commands={[enabledCommand(execute)]} />)
    const input = screen.getByRole('textbox', { name: 'Message' })

    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { value: '/search 한', selectionStart: 9, selectionEnd: 9 } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(execute).not.toHaveBeenCalled()
    fireEvent.compositionEnd(input)

    fireEvent.change(input, { target: { value: '/sea', selectionStart: 4, selectionEnd: 4 } })
    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible())
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByRole('listbox', { name: 'Commands' })).not.toBeInTheDocument()
  })

  it('does not swallow Enter when a slash query has no matching command', async () => {
    const execute = vi.fn()
    render(<Harness commands={[enabledCommand(execute)]} />)
    const input = screen.getByRole('textbox', { name: 'Message' })

    fireEvent.change(input, {
      target: { value: 'read /skill-drafts/example', selectionStart: 26, selectionEnd: 26 },
    })

    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible())
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
    expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(true)
    expect(execute).not.toHaveBeenCalled()
  })

  it('selects a typed resource without leaving a prompt-only mention token', async () => {
    const onResourceSelect = vi.fn()
    render(<Harness commands={[]} onResourceSelect={onResourceSelect} />)
    const input = screen.getByRole('textbox', { name: 'Message' })

    fireEvent.change(input, { target: { value: '@회', selectionStart: 2, selectionEnd: 2 } })
    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Resources' })).toBeVisible())
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onResourceSelect).toHaveBeenCalledWith({
      kind: 'file',
      id: '8f65c7bb-7092-43f4-a215-8e603101d114',
      label: '회의록',
    })
    expect(input).toHaveValue(' ')
  })
})
