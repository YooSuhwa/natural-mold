'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { useProbeMcpServer } from '@/lib/hooks/use-mcp-servers'
import type { McpProbeRequest, McpProbeTool } from '@/lib/types/mcp'

import type { McpProbeState, McpWizardTab } from './mcp-wizard-form-state'

type UseMcpWizardProbeControllerOptions = {
  readonly tab: McpWizardTab
  readonly basicsValid: boolean
  readonly probePayload: McpProbeRequest
}

type McpWizardProbeController = {
  readonly discoveredTools: readonly McpProbeTool[]
  readonly probeState: McpProbeState
  readonly testing: boolean
  readonly runProbe: () => Promise<void>
  readonly resetProbePreview: () => void
}

export function useMcpWizardProbeController({
  tab,
  basicsValid,
  probePayload,
}: UseMcpWizardProbeControllerOptions): McpWizardProbeController {
  const t = useTranslations('mcp.wizard')
  const probe = useProbeMcpServer()
  const [discoveredTools, setDiscoveredTools] = useState<McpProbeTool[]>([])
  const [probeState, setProbeState] = useState<McpProbeState>({ kind: 'idle' })
  const lastSuccessfulProbeKeyRef = useRef<string | null>(null)
  const inFlightProbeKeyRef = useRef<string | null>(null)
  const latestProbeKeyRef = useRef('')
  const probeRequestSeqRef = useRef(0)

  const probeKey = useMemo(() => JSON.stringify(probePayload), [probePayload])

  const resetProbePreview = useCallback(() => {
    probeRequestSeqRef.current += 1
    lastSuccessfulProbeKeyRef.current = null
    inFlightProbeKeyRef.current = null
    setDiscoveredTools([])
    setProbeState({ kind: 'idle' })
  }, [])

  const runProbe = useCallback(async (): Promise<void> => {
    if (!basicsValid) {
      toast.error(t('toast.required'))
      return
    }
    const currentProbeKey = probeKey
    const requestSeq = probeRequestSeqRef.current + 1
    probeRequestSeqRef.current = requestSeq
    inFlightProbeKeyRef.current = currentProbeKey
    setProbeState({ kind: 'pending' })
    try {
      const result = await probe.mutateAsync(probePayload)
      if (
        requestSeq !== probeRequestSeqRef.current ||
        currentProbeKey !== latestProbeKeyRef.current
      ) {
        return
      }
      if (!result.success) {
        const msg = result.error ?? t('toast.probeFailed')
        lastSuccessfulProbeKeyRef.current = null
        setDiscoveredTools([])
        setProbeState({ kind: 'error', message: msg })
        toast.error(msg)
        return
      }
      setDiscoveredTools(result.tools)
      lastSuccessfulProbeKeyRef.current = currentProbeKey
      setProbeState({ kind: 'ok', toolCount: result.tools.length })
    } catch (e) {
      if (
        requestSeq !== probeRequestSeqRef.current ||
        currentProbeKey !== latestProbeKeyRef.current
      ) {
        return
      }
      const msg = e instanceof Error ? e.message : t('toast.probeFailed')
      lastSuccessfulProbeKeyRef.current = null
      setDiscoveredTools([])
      setProbeState({ kind: 'error', message: msg })
      toast.error(msg)
    } finally {
      if (inFlightProbeKeyRef.current === currentProbeKey) {
        inFlightProbeKeyRef.current = null
      }
    }
  }, [basicsValid, probe, probeKey, probePayload, t])

  useEffect(() => {
    latestProbeKeyRef.current = probeKey
    const hasStaleSuccess =
      lastSuccessfulProbeKeyRef.current !== null && lastSuccessfulProbeKeyRef.current !== probeKey
    const hasStalePending =
      inFlightProbeKeyRef.current !== null && inFlightProbeKeyRef.current !== probeKey
    if (!hasStaleSuccess && !hasStalePending) return
    resetProbePreview()
  }, [probeKey, resetProbePreview])

  useEffect(() => {
    if (tab !== 'tools') return
    if (!basicsValid) return
    if (lastSuccessfulProbeKeyRef.current === probeKey) return
    if (inFlightProbeKeyRef.current === probeKey) return
    if (probe.isPending) return
    const timeoutId = window.setTimeout(() => {
      void runProbe()
    }, 0)
    return () => window.clearTimeout(timeoutId)
  }, [tab, basicsValid, probe.isPending, probeKey, runProbe])

  return {
    discoveredTools,
    probeState,
    testing: probe.isPending,
    runProbe,
    resetProbePreview,
  }
}
