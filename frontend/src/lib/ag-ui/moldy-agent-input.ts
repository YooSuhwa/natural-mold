import type { Message, RunAgentInput } from '@ag-ui/core'
import { z } from 'zod'
import { resourceContextReferencesSchema } from '@/lib/chat/context/resource-context'

const attachmentId = z.string().uuid()
const commandOptions = z
  .object({
    attachmentIds: z.array(attachmentId).optional(),
    resourceContext: z.object({ resources: resourceContextReferencesSchema }).strict().optional(),
    checkpointId: z.string().min(1).optional(),
    expectedHead: z.string().min(1).optional(),
    operation: z.enum(['edit', 'regenerate', 'retry']).optional(),
    retryInputId: z.string().uuid().optional(),
    strategy: z.enum(['enqueue', 'interrupt']).optional(),
  })
  .strict()

export type MoldyRunOptions = z.input<typeof commandOptions>

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function normalizeDelta(message: Message, options: MoldyRunOptions): Message {
  if (message.role !== 'user') throw new Error('A new chat command requires a user message.')
  if (typeof message.content === 'string') {
    return { role: 'user', id: message.id, content: message.content }
  }
  const fileIds = new Set<string>()
  const content = message.content.map((part) => {
    if (part.type === 'text') return { type: 'text' as const, text: part.text }
    const { source } = part
    if (source.type !== 'file' || (source.provider !== undefined && source.provider !== 'moldy')) {
      throw new Error('The attachment has not been uploaded to this conversation.')
    }
    const id = attachmentId.parse(source.value)
    fileIds.add(id)
    return {
      type: part.type,
      source: {
        type: 'file' as const,
        value: id,
        provider: 'moldy',
        ...(source.mimeType && { mimeType: source.mimeType }),
      },
    }
  })
  if (fileIds.size > 0) {
    if (
      options.attachmentIds &&
      (new Set(options.attachmentIds).size !== fileIds.size ||
        options.attachmentIds.some((id) => !fileIds.has(id)))
    ) {
      throw new Error('The submitted attachment references do not match.')
    }
    options.attachmentIds = [...fileIds]
  }
  return { role: 'user', id: message.id, content }
}

/** Persisted chat state, credentials and tool definitions are resolved by the worker. */
export function moldyCommandInput(input: RunAgentInput, conversationId: string): RunAgentInput {
  if (input.threadId !== conversationId) throw new Error('Chat thread changed during submission.')
  const command: RunAgentInput = {
    threadId: conversationId,
    runId: input.runId,
    protocolVersion: '1.0',
    messages: [],
    state: {},
    tools: [],
    context: [],
    forwardedProps: {},
  }
  // assistant-ui reuses the previous runConfig when answering an interrupt.
  // A resume carries only its slot decisions, including after a fork or GET attach.
  if (input.resume?.length) return { ...command, resume: input.resume }

  const props = record(input.forwardedProps)
  const config = record(props.runConfig)
  const options = commandOptions.parse(config.moldy ?? props.moldy ?? {})
  if (config.resource_context !== undefined) {
    const resources = resourceContextReferencesSchema.parse(config.resource_context)
    options.resourceContext = { resources }
  }

  if (options.operation !== 'regenerate' && options.operation !== 'retry') {
    const delta = input.messages.at(-1)
    if (!delta) throw new Error('A new chat command requires a message.')
    command.messages = [normalizeDelta(delta, options)]
  }
  if (Object.keys(options).length) command.forwardedProps = { moldy: options }
  return command
}
