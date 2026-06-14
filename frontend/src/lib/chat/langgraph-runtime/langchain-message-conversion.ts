'use client'

import { convertLangChainBaseMessage } from '@assistant-ui/react-langchain'
import type { useExternalMessageConverter } from '@assistant-ui/react'
import type { BaseMessage } from '@langchain/core/messages'
import type { TokenUsageBreakdown } from '@/lib/types'
import { isRecord, usageFromMessage } from './usage-normalization'

type ConvertedMessage = useExternalMessageConverter.Message
type ConvertedMessageResult = ReturnType<typeof convertLangChainBaseMessage>
type ConverterMetadata = Parameters<typeof convertLangChainBaseMessage>[1]

type MetadataCarrier = ConvertedMessage & {
  readonly metadata?: unknown
  readonly role?: string
}

function attachUsageMetadata(
  converted: ConvertedMessage,
  usage: TokenUsageBreakdown | null,
): ConvertedMessage {
  if (!usage) return converted

  const carrier = converted as MetadataCarrier
  if (carrier.role === 'tool') return converted

  const metadata = isRecord(carrier.metadata) ? carrier.metadata : {}
  const custom = isRecord(metadata.custom) ? metadata.custom : {}
  return {
    ...converted,
    metadata: {
      ...metadata,
      custom: {
        ...custom,
        usage,
      },
    },
  } as ConvertedMessage
}

export function convertMoldyLangChainMessage(
  message: BaseMessage,
  metadata: ConverterMetadata,
): ConvertedMessageResult {
  const usage = usageFromMessage(message)
  const converted = convertLangChainBaseMessage(message, metadata)
  if (Array.isArray(converted)) {
    return converted.map((item) => attachUsageMetadata(item, usage)) as ConvertedMessageResult
  }
  return attachUsageMetadata(converted, usage) as ConvertedMessageResult
}
