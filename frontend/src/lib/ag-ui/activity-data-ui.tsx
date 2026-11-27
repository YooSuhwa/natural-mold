'use client'

import { makeAssistantDataUI, type DataMessagePartProps } from '@assistant-ui/react'
import { DataUIDispatcher } from '@/lib/chat/data-ui'
import { isBoundAgUiDataCard } from './activity-card-owner'

function StandaloneDataCard(props: DataMessagePartProps<Record<string, unknown>>) {
  // The SDK places activity parts on the currently active message. A late
  // tool-bound card belongs beside the message containing its issued tool.
  if (!props.data || isBoundAgUiDataCard(props.data)) return null
  return <DataUIDispatcher {...props} />
}

const AgUiDataCard = makeAssistantDataUI<Record<string, unknown>>({
  name: 'agui-activity/moldy.ui_data',
  render: StandaloneDataCard,
})

export const MOLDY_AG_UI_DATA_UI = [AgUiDataCard] as const
