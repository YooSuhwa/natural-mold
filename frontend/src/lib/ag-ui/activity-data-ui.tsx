'use client'

import { makeAssistantDataUI } from '@assistant-ui/react'
import { DataUIDispatcher } from '@/lib/chat/data-ui'
import type { UIDataEventPayload } from '@/lib/types/ui-data'

const AgUiDataCard = makeAssistantDataUI<UIDataEventPayload>({
  name: 'agui-activity/moldy.ui_data',
  render: DataUIDispatcher,
})

export const MOLDY_AG_UI_DATA_UI = [AgUiDataCard] as const
