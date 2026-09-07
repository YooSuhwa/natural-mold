import type {
  ChatCommandActions,
  ChatCommandCopy,
  ChatCommandDefinition,
  ChatCommandId,
} from './chat-command-types'

type CommandAction = (argument: string) => void | Promise<void>

function command(
  id: ChatCommandId,
  copy: ChatCommandCopy,
  action: CommandAction | undefined,
  acceptsArgument: boolean,
  disabledReason: string,
): ChatCommandDefinition {
  return {
    id,
    ...copy[id],
    acceptsArgument,
    availability: action ? { kind: 'enabled' } : { kind: 'disabled', reason: disabledReason },
    execute: action ?? (() => {}),
  }
}

export function createChatCommands(
  actions: ChatCommandActions,
  copy: ChatCommandCopy,
  disabledReason: string,
): readonly ChatCommandDefinition[] {
  const retry = actions.retryLastFailedInput
  return [
    command('search', copy, actions.openTranscriptSearch, true, disabledReason),
    command('export', copy, actions.openExportChooser, false, disabledReason),
    command('files', copy, actions.openFilesRail, false, disabledReason),
    command('attach', copy, actions.openFilePicker, false, disabledReason),
    command('new', copy, actions.createNewConversation, false, disabledReason),
    command(
      'retry',
      copy,
      retry ? () => retry.execute(retry.failedInputId) : undefined,
      false,
      disabledReason,
    ),
    command('compact', copy, actions.compactConversation, false, disabledReason),
    command('stop', copy, actions.stopRunAndPauseQueue, false, disabledReason),
    command('help', copy, actions.openHelp, true, disabledReason),
  ]
}
