import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react'
import { fireEvent } from '@testing-library/react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ImeSafeComposerInput } from '@/components/chat/ime-safe-composer-input'
import { ChatEmptyState } from '@/components/chat/chat-empty-state'
import { ChatComposerTriggers } from '@/components/chat/context/chat-composer-triggers'
import type { ChatCommandDefinition } from '@/lib/chat/commands/chat-command-types'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'

vi.mock('next-intl', async () => {
  const actual = await vi.importActual<typeof import('next-intl')>('next-intl')
  return { ...actual, useTranslations: () => (key: string) => key }
})

vi.mock('@/components/agent/agent-avatar', () => ({
  AgentAvatar: ({ name }: { readonly name: string }) => <span>{name}</span>,
}))

vi.mock('@/lib/hooks/use-templates', () => ({
  useTemplates: () => ({ data: undefined }),
}))

const EMPTY_MESSAGES: ThreadMessageLike[] = []
const onNew = vi.fn(async () => undefined)

const COMMANDS: readonly ChatCommandDefinition[] = [
  {
    id: 'search',
    label: '/search',
    description: 'Search transcript',
    acceptsArgument: true,
    availability: { kind: 'enabled' },
    execute: vi.fn(),
  },
]

function CommandDiscoveryHarness() {
  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: EMPTY_MESSAGES,
    isRunning: false,
    onNew,
    convertMessage: (message) => message,
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ChatEmptyState agent={undefined} fallback="fallback" />
      <ChatComposerTriggers
        commands={COMMANDS}
        commandAriaLabel="Commands"
        resourceCandidates={[]}
        selectedResourceCount={0}
        resourceCategoryLabels={{
          artifact: 'Artifact',
          conversation: 'Conversation',
          file: 'File',
          skill: 'Skill',
        }}
        resourceAriaLabel="Resources"
        resourceLimitReason="Limit"
        onResourceSelect={() => {}}
      >
        <ImeSafeComposerInput aria-label="Message" autoFocusKey="empty-command" />
      </ChatComposerTriggers>
    </AssistantRuntimeProvider>
  )
}

async function flushComposerTriggerSync(): Promise<void> {
  await new Promise<void>((resolve) => window.setTimeout(resolve, 0))
}

describe('ChatEmptyState command discovery', () => {
  beforeEach(() => {
    onNew.mockClear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('places slash text in the real IME composer, focuses it, opens the existing catalog, and does not send', async () => {
    const user = userEvent.setup()
    render(<CommandDiscoveryHarness />)

    await user.click(screen.getByRole('button', { name: 'emptyState.commandDiscovery' }))

    const input = screen.getByRole('textbox', { name: 'Message' })
    expect(input).toHaveValue('/')
    await waitFor(() => expect(input).toHaveFocus())
    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible())
    expect(screen.getByRole('option', { name: /search/i })).toBeVisible()
    expect(onNew).not.toHaveBeenCalled()
  })

  it('opens the real catalog when focus and selection happen before external slash text reaches the textarea', async () => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(0)
      return 1
    })
    render(<CommandDiscoveryHarness />)

    // Given the actual trigger root and its registered slash adapter.
    // When the empty-state action focuses before React's external textarea sync.
    // Then the post-sync cursor notification still opens the real catalog.
    act(() => {
      screen.getByRole('button', { name: 'emptyState.commandDiscovery' }).click()
    })

    const input = screen.getByRole('textbox', { name: 'Message' })
    await waitFor(() => expect(input).toHaveValue('/'))
    expect(input).toHaveFocus()
    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible())
    expect(screen.getByRole('option', { name: /search/i })).toBeVisible()
    expect(onNew).not.toHaveBeenCalled()
  })

  it('keeps a real command catalog closed after Escape and its ARIA rerender', async () => {
    render(<CommandDiscoveryHarness />)
    const input = screen.getByRole('textbox', { name: 'Message' })

    fireEvent.change(input, { target: { selectionEnd: 4, selectionStart: 4, value: '/sea' } })
    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible())

    fireEvent.keyDown(input, { key: 'Escape' })
    await flushComposerTriggerSync()

    expect(screen.queryByRole('listbox', { name: 'Commands' })).not.toBeInTheDocument()
  })

  it('keeps real slash detection at the typed mid-text caret through an ARIA rerender', async () => {
    render(<CommandDiscoveryHarness />)
    const input = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message' })

    fireEvent.change(input, {
      target: { value: 'hi /sea there' },
    })
    input.setSelectionRange(7, 7)
    fireEvent.select(input)

    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible())
    await flushComposerTriggerSync()

    expect(input).toHaveValue('hi /sea there')
    expect(input).toHaveProperty('selectionStart', 7)
    expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible()
  })

  it('keeps real Korean IME completion selection while the slash trigger rerenders', async () => {
    render(<CommandDiscoveryHarness />)
    const input = screen.getByRole('textbox', { name: 'Message' })

    fireEvent.compositionStart(input)
    fireEvent.change(input, { target: { selectionEnd: 5, selectionStart: 5, value: 'hi /한' } })
    fireEvent.compositionEnd(input, {
      target: { selectionEnd: 5, selectionStart: 5, value: 'hi /한' },
    })
    await flushComposerTriggerSync()

    expect(input).toHaveValue('hi /한')
    expect(input).toHaveProperty('selectionStart', 5)
    await waitFor(() => expect(screen.getByRole('listbox', { name: 'Commands' })).toBeVisible())
  })
})
