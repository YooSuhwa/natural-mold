'use client'

import { convertLangChainBaseMessage } from '@assistant-ui/react-langchain'
import type { useExternalMessageConverter } from '@assistant-ui/react'
import type { BaseMessage } from '@langchain/core/messages'
import type { TokenUsageBreakdown } from '@/lib/types'
import type { UIDataItem } from '@/lib/types/ui-data'
import { MOLDY_UI_DATA_PART_NAME } from '@/lib/chat/data-ui-registry'
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

function attachUIDataParts(
  converted: ConvertedMessage,
  uiData: readonly UIDataItem[] | null,
): ConvertedMessage {
  if (!uiData || uiData.length === 0) return converted

  const carrier = converted as MetadataCarrier
  if (carrier.role !== 'assistant') return converted

  const content = (converted as { content?: unknown }).content
  if (!Array.isArray(content)) return converted

  // Path A producer: inject one ``moldy_ui`` data part per item carrying
  // ``{type, props}``. The registered ``makeAssistantDataUI`` dispatcher
  // (data-ui.tsx) renders it via the allowlist registry + Zod fail-safe.
  const dataParts = uiData.map((item) => ({
    type: 'data' as const,
    name: MOLDY_UI_DATA_PART_NAME,
    data: { type: item.type, props: item.props },
  }))
  return { ...converted, content: [...content, ...dataParts] } as ConvertedMessage
}

export function convertMoldyLangChainMessage(
  message: BaseMessage,
  metadata: ConverterMetadata,
): ConvertedMessageResult {
  const usage = usageFromMessage(message)
  const compaction = compactionFromMessage(message)
  const uiData = (message as BaseMessage & { uiData?: UIDataItem[] | null }).uiData ?? null
  const converted = convertLangChainBaseMessage(message, metadata)
  if (Array.isArray(converted)) {
    return converted.map((item) =>
      attachUIDataParts(
        attachCompactionMetadata(attachUsageMetadata(item, usage), compaction),
        uiData,
      ),
    ) as ConvertedMessageResult
  }
  return attachUIDataParts(
    attachCompactionMetadata(attachUsageMetadata(converted, usage), compaction),
    uiData,
  ) as ConvertedMessageResult
}
