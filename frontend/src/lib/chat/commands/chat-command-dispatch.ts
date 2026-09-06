import {
  CHAT_COMMAND_IDS,
  type ChatCommandDefinition,
  type ChatCommandId,
} from './chat-command-types'

type ParsedChatCommand =
  | { readonly kind: 'prompt' }
  | { readonly kind: 'unknown'; readonly name: string }
  | { readonly kind: 'command'; readonly commandId: ChatCommandId; readonly argument: string }

export type ChatCommandDispatchResult =
  | { readonly kind: 'prompt' }
  | { readonly kind: 'handled'; readonly commandId: ChatCommandId }
  | {
      readonly kind: 'blocked'
      readonly commandId?: ChatCommandId
      readonly reason: 'unknown' | 'disabled' | 'arguments-not-supported'
      readonly message?: string
    }

function isChatCommandId(value: string): value is ChatCommandId {
  return CHAT_COMMAND_IDS.some((id) => id === value)
}

export function parseChatCommand(text: string): ParsedChatCommand {
  const trimmed = text.trim()
  if (!trimmed.startsWith('/')) return { kind: 'prompt' }

  const [rawName = '', ...argumentParts] = trimmed.slice(1).split(/\s+/u)
  const name = rawName.toLowerCase()
  if (!isChatCommandId(name)) return { kind: 'unknown', name }
  return { kind: 'command', commandId: name, argument: argumentParts.join(' ') }
}

export async function dispatchChatCommand(
  text: string,
  commands: readonly ChatCommandDefinition[],
): Promise<ChatCommandDispatchResult> {
  const parsed = parseChatCommand(text)
  if (parsed.kind === 'prompt') return parsed
  if (parsed.kind === 'unknown') return { kind: 'blocked', reason: 'unknown' }

  const definition = commands.find((candidate) => candidate.id === parsed.commandId)
  if (!definition) return { kind: 'blocked', commandId: parsed.commandId, reason: 'unknown' }
  if (parsed.argument && !definition.acceptsArgument) {
    return {
      kind: 'blocked',
      commandId: definition.id,
      reason: 'arguments-not-supported',
    }
  }
  if (definition.availability.kind === 'disabled') {
    return {
      kind: 'blocked',
      commandId: definition.id,
      reason: 'disabled',
      message: definition.availability.reason,
    }
  }
  await definition.execute(parsed.argument)
  return { kind: 'handled', commandId: definition.id }
}
