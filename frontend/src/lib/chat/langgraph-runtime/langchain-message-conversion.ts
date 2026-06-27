'use client'

import { convertLangChainBaseMessage } from '@assistant-ui/react-langchain'
import type { useExternalMessageConverter } from '@assistant-ui/react'
import type { BaseMessage } from '@langchain/core/messages'
import type { TokenUsageBreakdown } from '@/lib/types'
import { compactionFromMessage, type CompactionMarker } from './compaction-events'
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

function attachCompactionMetadata(
  converted: ConvertedMessage,
  compaction: CompactionMarker | null,
): ConvertedMessage {
  if (!compaction) return converted

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
        compaction,
      },
    },
  } as ConvertedMessage
}

export function convertMoldyLangChainMessage(
  message: BaseMessage,
  metadata: ConverterMetadata,
): ConvertedMessageResult {
  const usage = usageFromMessage(message)
  const compaction = compactionFromMessage(message)
  const converted = convertLangChainBaseMessage(message, metadata)
  if (Array.isArray(converted)) {
    return converted.map((item) =>
      attachCompactionMetadata(attachUsageMetadata(item, usage), compaction),
    ) as ConvertedMessageResult
  }
  return attachCompactionMetadata(
    attachUsageMetadata(converted, usage),
    compaction,
  ) as ConvertedMessageResult
}
