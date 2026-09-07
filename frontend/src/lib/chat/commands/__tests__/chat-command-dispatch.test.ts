import { describe, expect, it, vi } from 'vitest'

import { createChatCommands } from '../chat-command-catalog'
import { dispatchChatCommand, parseChatCommand } from '../chat-command-dispatch'

const copy = {
  search: { label: '/search', description: 'Search transcript' },
  export: { label: '/export', description: 'Export conversation' },
  files: { label: '/files', description: 'Open files' },
  attach: { label: '/attach', description: 'Attach file' },
  new: { label: '/new', description: 'New conversation' },
  retry: { label: '/retry', description: 'Retry failed input' },
  compact: { label: '/compact', description: 'Compact context' },
  stop: { label: '/stop', description: 'Stop run' },
  help: { label: '/help', description: 'Show commands' },
} as const

describe('chat command dispatch', () => {
  it('characterizes ordinary prompt text as model input', () => {
    expect(parseChatCommand('일반 질문입니다')).toEqual({ kind: 'prompt' })
  })

  it('blocks unknown commands and arguments outside local search/help', async () => {
    const commands = createChatCommands({}, copy, 'Unavailable')

    await expect(dispatchChatCommand('/missing', commands)).resolves.toMatchObject({
      kind: 'blocked',
      reason: 'unknown',
    })
    await expect(dispatchChatCommand('/export private', commands)).resolves.toMatchObject({
      kind: 'blocked',
      reason: 'arguments-not-supported',
    })
  })

  it('invokes the real callback and forwards only local search/help arguments', async () => {
    const openTranscriptSearch = vi.fn()
    const openHelp = vi.fn()
    const commands = createChatCommands({ openTranscriptSearch, openHelp }, copy, 'Unavailable')

    await expect(dispatchChatCommand('/search 오류', commands)).resolves.toEqual({
      kind: 'handled',
      commandId: 'search',
    })
    await expect(dispatchChatCommand('/help file', commands)).resolves.toEqual({
      kind: 'handled',
      commandId: 'help',
    })
    expect(openTranscriptSearch).toHaveBeenCalledWith('오류')
    expect(openHelp).toHaveBeenCalledWith('file')
  })

  it('dispatches every supported action boundary exactly once', async () => {
    const actions = {
      openTranscriptSearch: vi.fn(),
      openExportChooser: vi.fn(),
      openFilesRail: vi.fn(),
      openFilePicker: vi.fn(),
      createNewConversation: vi.fn(),
      retryLastFailedInput: { failedInputId: 'failed-2', execute: vi.fn() },
      compactConversation: vi.fn(),
      stopRunAndPauseQueue: vi.fn(),
      openHelp: vi.fn(),
    }
    const commands = createChatCommands(actions, copy, 'Unavailable')

    for (const input of [
      '/search needle',
      '/export',
      '/files',
      '/attach',
      '/new',
      '/retry',
      '/compact',
      '/stop',
      '/help retry',
    ]) {
      await dispatchChatCommand(input, commands)
    }

    expect(actions.openTranscriptSearch).toHaveBeenCalledWith('needle')
    expect(actions.openExportChooser).toHaveBeenCalledOnce()
    expect(actions.openFilesRail).toHaveBeenCalledOnce()
    expect(actions.openFilePicker).toHaveBeenCalledOnce()
    expect(actions.createNewConversation).toHaveBeenCalledOnce()
    expect(actions.retryLastFailedInput.execute).toHaveBeenCalledWith('failed-2')
    expect(actions.compactConversation).toHaveBeenCalledOnce()
    expect(actions.stopRunAndPauseQueue).toHaveBeenCalledOnce()
    expect(actions.openHelp).toHaveBeenCalledWith('retry')
  })

  it('never retries an accepted pending input through the retry command', async () => {
    const retryLastFailedInput = vi.fn()
    const commands = createChatCommands(
      { retryLastFailedInput: { failedInputId: 'failed-1', execute: retryLastFailedInput } },
      copy,
      'Unavailable',
    )

    await dispatchChatCommand('/retry', commands)

    expect(retryLastFailedInput).toHaveBeenCalledWith('failed-1')
  })

  it('visibly blocks unavailable actions instead of reporting success', async () => {
    const commands = createChatCommands({}, copy, 'Not available here')

    await expect(dispatchChatCommand('/compact', commands)).resolves.toEqual({
      kind: 'blocked',
      commandId: 'compact',
      reason: 'disabled',
      message: 'Not available here',
    })
  })
})
