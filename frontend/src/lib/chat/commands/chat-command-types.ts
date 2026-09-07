export const CHAT_COMMAND_IDS = [
  'search',
  'export',
  'files',
  'attach',
  'new',
  'retry',
  'compact',
  'stop',
  'help',
] as const

export type ChatCommandId = (typeof CHAT_COMMAND_IDS)[number]

export type ChatCommandAvailability =
  | { readonly kind: 'enabled' }
  | { readonly kind: 'disabled'; readonly reason: string }

export type ChatCommandDefinition = {
  readonly id: ChatCommandId
  readonly label: string
  readonly description: string
  readonly acceptsArgument: boolean
  readonly availability: ChatCommandAvailability
  readonly execute: (argument: string) => void | Promise<void>
}

export type ChatCommandCopy = Readonly<
  Record<ChatCommandId, { readonly label: string; readonly description: string }>
>

type ChatCommandCallback = (argument: string) => void | Promise<void>

export type ChatCommandActions = {
  readonly openTranscriptSearch?: ChatCommandCallback
  readonly openExportChooser?: () => void | Promise<void>
  readonly openFilesRail?: () => void | Promise<void>
  readonly openFilePicker?: () => void | Promise<void>
  readonly createNewConversation?: () => void | Promise<void>
  readonly retryLastFailedInput?: {
    readonly failedInputId: string
    readonly execute: (failedInputId: string) => void | Promise<void>
  }
  readonly compactConversation?: () => void | Promise<void>
  readonly stopRunAndPauseQueue?: () => void | Promise<void>
  readonly openHelp?: ChatCommandCallback
}
