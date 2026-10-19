'use client'

import { useCallback } from 'react'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import { useSetAtom } from 'jotai'
import {
  protocolEventRunId,
  protocolExcludedCapabilities,
  protocolRunStart,
} from '@/lib/chat/excluded-capabilities'
import {
  setExcludedCapabilitiesAtom,
  startExcludedCapabilitiesRunAtom,
} from '@/lib/stores/chat-excluded-capabilities'

const CHANNELS = ['lifecycle', 'custom'] as const

/**
 * Subscribe to run starts and the `moldy.excluded_capabilities` run-start event
 * (F-06 / plan 13.6).
 *
 * A root `running` lifecycle event clears the banner (the backend emits the exclusion
 * event only when non-empty, so a clean run must hide it). `replay: true` restores the
 * banner on reload/re-entry in event order; run ids come from the backend's stable
 * event ids (`<run_id>:…`), so an older run's replay never resurrects its notice.
 */
export function useLangGraphExcludedCapabilitiesEffects({
  stream,
  conversationId,
}: {
  readonly stream: AnyStream
  readonly conversationId: string
}): void {
  const setExcluded = useSetAtom(setExcludedCapabilitiesAtom)
  const startRun = useSetAtom(startExcludedCapabilitiesRunAtom)
  const handleEvent = useCallback(
    (event: Event) => {
      const startedRun = protocolRunStart(event)
      if (startedRun) {
        startRun({ conversationId, runId: startedRun })
        return
      }
      const groups = protocolExcludedCapabilities(event)
      if (!groups) return
      setExcluded({ conversationId, runId: protocolEventRunId(event), groups })
    },
    [conversationId, setExcluded, startRun],
  )

  useChannelEffect(stream, CHANNELS, { replay: true, bufferSize: 300, onEvent: handleEvent })
}
