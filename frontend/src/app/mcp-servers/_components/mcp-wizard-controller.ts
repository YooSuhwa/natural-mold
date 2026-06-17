'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'

import { useStartOAuth2 } from '@/lib/hooks/use-credential-test'
import {
  useCreateFromRegistry,
  useCreateMcpServer,
  useDiscoverMcpTools,
  useMcpRegistry,
} from '@/lib/hooks/use-mcp-servers'
import { API_BASE } from '@/lib/api/client'
import { credentialQueryKeys } from '@/lib/query-keys/credentials'
import type { McpProbeTool, McpRegistryEntry, McpTransport } from '@/lib/types/mcp'

import {
  appendMcpArgDraft,
  buildMcpOAuthInitialData,
  buildMcpOAuthInitialName,
  buildMcpProbePayload,
  buildMcpRegistryPayload,
  buildMcpServerPayload,
  clearMcpWizardRegistrySelection,
  createInitialMcpWizardState,
  createMcpWizardStateFromRegistryEntry,
  isMcpWizardBasicsValid,
  isOAuthCompletedMessage,
  type McpProbeState,
  type McpWizardFormPatch,
  type McpWizardFormState,
  type McpWizardTab,
} from './mcp-wizard-form-state'
import { useMcpWizardProbeController } from './mcp-wizard-probe-controller'

type UseMcpWizardControllerOptions = {
  readonly onClose: () => void
}

export type McpWizardController = {
  readonly tab: McpWizardTab
  readonly form: McpWizardFormState
  readonly registry: readonly McpRegistryEntry[]
  readonly basicsValid: boolean
  readonly usesMcpOAuth: boolean
  readonly discoveredTools: readonly McpProbeTool[]
  readonly probeState: McpProbeState
  readonly testing: boolean
  readonly saving: boolean
  readonly credentialCreateOpen: boolean
  readonly oauthStarting: boolean
  readonly oauthWaiting: boolean
  readonly oauthConnected: boolean
  readonly oauthCredentialInitialName: string
  readonly oauthCredentialInitialData: Record<string, string | boolean>
  readonly setTab: (tab: McpWizardTab) => void
  readonly setCredentialCreateOpen: (open: boolean) => void
  readonly updateForm: (patch: McpWizardFormPatch) => void
  readonly handleTabChange: (value: string) => void
  readonly handlePickRegistryEntry: (entry: McpRegistryEntry) => void
  readonly clearRegistry: () => void
  readonly handleTransportChange: (transport: McpTransport) => void
  readonly handleAddArg: () => void
  readonly handleOAuthConnect: () => Promise<void>
  readonly runProbe: () => Promise<void>
  readonly handleSave: () => Promise<void>
  readonly handleCreatedCredential: (credentialId: string) => void
}

export function useMcpWizardController({
  onClose,
}: UseMcpWizardControllerOptions): McpWizardController {
  const t = useTranslations('mcp.wizard')
  const queryClient = useQueryClient()
  const { data: registry } = useMcpRegistry()
  const create = useCreateMcpServer()
  const createFromRegistry = useCreateFromRegistry()
  const discover = useDiscoverMcpTools()
  const startOAuth = useStartOAuth2()

  const [tab, setTab] = useState<McpWizardTab>('basics')
  const [form, setForm] = useState(createInitialMcpWizardState)
  const [credentialCreateOpen, setCredentialCreateOpen] = useState(false)
  const [oauthPendingCredentialId, setOauthPendingCredentialId] = useState<string | null>(null)
  const [oauthConnectedCredentialId, setOauthConnectedCredentialId] = useState<string | null>(null)

  const updateForm = useCallback((patch: McpWizardFormPatch) => {
    setForm((prev) => ({ ...prev, ...patch }))
  }, [])

  const basicsValid = useMemo(() => isMcpWizardBasicsValid(form), [form])
  const probePayload = useMemo(() => buildMcpProbePayload(form), [form])
  const { discoveredTools, probeState, testing, runProbe, resetProbePreview } =
    useMcpWizardProbeController({
      tab,
      basicsValid,
      probePayload,
    })
  const selectedRegistryEntry = useMemo(() => {
    if (!form.registryKey) return null
    return (registry ?? []).find((entry) => entry.key === form.registryKey) ?? null
  }, [registry, form.registryKey])
  const usesMcpOAuth = form.credentialDefinitionFilter === 'mcp_oauth2'
  const oauthCredentialInitialData = useMemo(
    () => buildMcpOAuthInitialData(form, selectedRegistryEntry),
    [form, selectedRegistryEntry],
  )
  const oauthCredentialInitialName = useMemo(
    () => buildMcpOAuthInitialName(form, selectedRegistryEntry),
    [form, selectedRegistryEntry],
  )

  function handlePickRegistryEntry(entry: McpRegistryEntry) {
    setForm(createMcpWizardStateFromRegistryEntry(entry))
    resetProbePreview()
  }

  function clearRegistry() {
    setForm((prev) => clearMcpWizardRegistrySelection(prev))
    setOauthPendingCredentialId(null)
    setOauthConnectedCredentialId(null)
    resetProbePreview()
  }

  useEffect(() => {
    function handleOAuthMessage(event: MessageEvent) {
      if (!isOAuthCompletedMessage(event.data)) return

      const allowedOrigins = new Set([window.location.origin])
      if (URL.canParse(API_BASE)) {
        allowedOrigins.add(new URL(API_BASE).origin)
      }
      if (!allowedOrigins.has(event.origin)) return

      const completedCredentialId = event.data.credentialId ?? null
      if (
        completedCredentialId &&
        form.credentialId &&
        completedCredentialId !== form.credentialId
      ) {
        return
      }

      setOauthPendingCredentialId(null)
      setOauthConnectedCredentialId(completedCredentialId ?? form.credentialId)
      resetProbePreview()
      void queryClient.invalidateQueries({ queryKey: credentialQueryKeys.all })
      if (completedCredentialId) {
        void queryClient.invalidateQueries({
          queryKey: credentialQueryKeys.detail(completedCredentialId),
        })
      }
      toast.success(t('auth.oauthConnected'))
    }

    window.addEventListener('message', handleOAuthMessage)
    return () => window.removeEventListener('message', handleOAuthMessage)
  }, [form.credentialId, queryClient, resetProbePreview, t])

  async function handleOAuthConnect(): Promise<void> {
    if (!form.credentialId) {
      toast.error(t('auth.oauthSelectFirst'))
      return
    }
    const popup = window.open('', 'moldy-mcp-oauth', 'popup,width=560,height=760')
    if (!popup) {
      toast.error(t('auth.oauthPopupBlocked'))
      return
    }
    try {
      const { authorization_url: authorizationUrl } = await startOAuth.mutateAsync(
        form.credentialId,
      )
      popup.location.href = authorizationUrl
      setOauthPendingCredentialId(form.credentialId)
      setOauthConnectedCredentialId(null)
      popup.focus()
    } catch (e) {
      popup.close()
      toast.error(e instanceof Error ? e.message : t('auth.oauthStartFailed'))
    }
  }

  function handleAddArg() {
    setForm((prev) => appendMcpArgDraft(prev))
  }

  function handleTransportChange(transport: McpTransport) {
    updateForm({ transport })
    resetProbePreview()
  }

  function handleTabChange(value: string) {
    if (value === 'basics' || value === 'auth' || value === 'tools') {
      setTab(value)
    }
  }

  async function handleSave(): Promise<void> {
    if (!basicsValid) {
      toast.error(t('toast.required'))
      setTab('basics')
      return
    }
    try {
      const server = form.registryKey
        ? await createFromRegistry.mutateAsync(buildMcpRegistryPayload(form))
        : await create.mutateAsync(buildMcpServerPayload(form))
      try {
        await discover.mutateAsync(server.id)
      } catch {
        toast.warning(t('toast.importFailedAfterSave'))
      }
      toast.success(t('toast.added'))
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.addFailed'))
    }
  }

  function handleCreatedCredential(credentialId: string) {
    updateForm({ credentialId })
    setOauthConnectedCredentialId(null)
    setOauthPendingCredentialId(null)
  }

  return {
    tab,
    form,
    registry: registry ?? [],
    basicsValid,
    usesMcpOAuth,
    discoveredTools,
    probeState,
    testing,
    saving: create.isPending || createFromRegistry.isPending || discover.isPending,
    credentialCreateOpen,
    oauthStarting: startOAuth.isPending,
    oauthWaiting: oauthPendingCredentialId === form.credentialId,
    oauthConnected: oauthConnectedCredentialId === form.credentialId,
    oauthCredentialInitialName,
    oauthCredentialInitialData,
    setTab,
    setCredentialCreateOpen,
    updateForm,
    handleTabChange,
    handlePickRegistryEntry,
    clearRegistry,
    handleTransportChange,
    handleAddArg,
    handleOAuthConnect,
    runProbe,
    handleSave,
    handleCreatedCredential,
  }
}
