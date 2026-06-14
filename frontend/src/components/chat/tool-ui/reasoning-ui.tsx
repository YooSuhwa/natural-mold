'use client'

import { makeAssistantDataUI, type DataMessagePartProps } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import { BrainIcon } from 'lucide-react'
import { CollapsiblePill, type PillStatus } from './collapsible-pill'

interface ReasoningData {
  readonly summary?: unknown
  readonly text?: unknown
  readonly message?: unknown
  readonly status?: unknown
}

interface ReasoningDataViewProps {
  readonly data: ReasoningData
  readonly statusType?: string
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function reasoningSummary(data: ReasoningData): string | undefined {
  return textValue(data.summary) ?? textValue(data.text) ?? textValue(data.message)
}

function pillStatus(statusType: string | undefined): PillStatus {
  if (statusType === 'complete') return 'success'
  if (statusType === 'incomplete') return 'cancelled'
  return statusType === 'error' ? 'error' : 'loading'
}

export function ReasoningDataView({ data, statusType }: ReasoningDataViewProps) {
  const t = useTranslations('chat.reasoning')
  const summary = reasoningSummary(data)
  const status = textValue(data.status) ?? t(statusType === 'complete' ? 'ready' : 'running')

  return (
    <div className="my-2">
      <CollapsiblePill
        status={pillStatus(statusType)}
        kind="thinking"
        title={t('title')}
        meta={status}
        leadingIcon={BrainIcon}
        defaultExpanded={Boolean(summary)}
      >
        {summary ? (
          <p className="text-xs leading-relaxed text-muted-foreground">{summary}</p>
        ) : null}
      </CollapsiblePill>
    </div>
  )
}

function ReasoningDataPart({ data, status }: DataMessagePartProps<ReasoningData>) {
  return <ReasoningDataView data={data} statusType={status?.type} />
}

export const ReasoningDataUI = makeAssistantDataUI<ReasoningData>({
  name: 'reasoning',
  render: ReasoningDataPart,
})
