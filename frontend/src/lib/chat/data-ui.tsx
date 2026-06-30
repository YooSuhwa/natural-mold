'use client'

import { useMemo } from 'react'
import { makeAssistantDataUI, type DataMessagePartProps } from '@assistant-ui/react'
import { ReasoningDataUI } from '@/components/chat/tool-ui/reasoning-ui'
import { MOLDY_UI_DATA_PART_NAME, resolveDataUI } from './data-ui-registry'

/**
 * Generative UI render path (chat-generative-ui-dev-plan §5.2, path A). The
 * producer injects a single ``moldy_ui`` data part carrying ``{type, props}``;
 * this dispatcher resolves it through the allowlist registry (Zod + fail-safe)
 * and renders the matching component. Reuses the existing ``case 'data'`` /
 * ``dataUI`` machinery in ``assistant-thread.tsx``.
 */

interface MoldyUIData {
  readonly type?: unknown
  readonly props?: unknown
}

function DataUIDispatcher({ data }: DataMessagePartProps<MoldyUIData>) {
  const type = typeof data?.type === 'string' ? data.type : null
  const rawProps = data?.props
  // Memoize the Zod parse on the (stable, converter-cached) data reference so
  // the resolved props keep a stable identity across re-renders.
  const resolved = useMemo(() => (type ? resolveDataUI(type, rawProps) : null), [type, rawProps])
  if (!resolved) return null
  const { Component, props } = resolved
  return <Component {...props} />
}

export const MoldyDataUI = makeAssistantDataUI<MoldyUIData>({
  name: MOLDY_UI_DATA_PART_NAME,
  render: DataUIDispatcher,
})

// Registered via the ``dataUI`` prop on ``AssistantThread`` (chat-runtime-section).
export const ALL_DATA_UI = [ReasoningDataUI, MoldyDataUI] as const
