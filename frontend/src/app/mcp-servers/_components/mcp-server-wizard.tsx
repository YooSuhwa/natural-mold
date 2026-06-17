'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { Activity, CheckCircle2, KeyRound, Loader2, Plus, X, XCircle } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import { DomainIconTile } from '@/components/shared/icon'
import { DialogShell } from '@/components/shared/dialog-shell'
import {
  useCreateFromRegistry,
  useCreateMcpServer,
  useDiscoverMcpTools,
  useMcpRegistry,
  useProbeMcpServer,
} from '@/lib/hooks/use-mcp-servers'
import { useStartOAuth2 } from '@/lib/hooks/use-credential-test'
import { API_BASE } from '@/lib/api/client'
import type { McpProbeRequest, McpProbeTool, McpRegistryEntry, McpTransport } from '@/lib/types/mcp'

interface McpServerWizardProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

type TabKey = 'basics' | 'auth' | 'tools'
type ProbeState =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'ok'; toolCount: number }
  | { kind: 'error'; message: string }

/**
 * Compact 3-tab MCP wizard. Tabs are freely navigable (no step gates), so
 * the user can flip back-and-forth while configuring; the actual save only
 * happens from the Tools tab once a probe + tool selection are in hand.
 */
export function McpServerWizard({ open, onOpenChange }: McpServerWizardProps) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg" height="tall">
      {open ? <McpWizardBody onClose={() => onOpenChange(false)} /> : null}
    </DialogShell>
  )
}

function McpWizardBody({ onClose }: { onClose: () => void }) {
  const t = useTranslations('mcp.wizard')
  const queryClient = useQueryClient()
  const { data: registry } = useMcpRegistry()
  const create = useCreateMcpServer()
  const createFromRegistry = useCreateFromRegistry()
  const discover = useDiscoverMcpTools()
  const probe = useProbeMcpServer()
  const startOAuth = useStartOAuth2()

  const [tab, setTab] = useState<TabKey>('basics')

  // -- form state ----------------------------------------------------------
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [transport, setTransport] = useState<McpTransport>('streamable_http')
  const [url, setUrl] = useState('')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState<string[]>([])
  const [argDraft, setArgDraft] = useState('')
  const [envVars, setEnvVars] = useState<Array<{ key: string; value: string }>>([])
  const [headers, setHeaders] = useState<Array<{ key: string; value: string }>>([])
  const [credentialId, setCredentialId] = useState<string | null>(null)
  const [registryKey, setRegistryKey] = useState<string | null>(null)
  const [credentialDefinitionFilter, setCredentialDefinitionFilter] = useState<string | null>(null)
  const [credentialCreateOpen, setCredentialCreateOpen] = useState(false)
  const [oauthPendingCredentialId, setOauthPendingCredentialId] = useState<string | null>(null)
  const [oauthConnectedCredentialId, setOauthConnectedCredentialId] = useState<string | null>(null)

  // -- preview state -------------------------------------------------------
  const [discoveredTools, setDiscoveredTools] = useState<McpProbeTool[]>([])
  const [probeState, setProbeState] = useState<ProbeState>({ kind: 'idle' })
  const lastSuccessfulProbeKeyRef = useRef<string | null>(null)
  const inFlightProbeKeyRef = useRef<string | null>(null)
  const latestProbeKeyRef = useRef('')
  const probeRequestSeqRef = useRef(0)

  const basicsValid = useMemo(() => {
    if (!name.trim()) return false
    if (registryKey) return true
    if (transport === 'stdio') return command.trim().length > 0
    return url.trim().length > 0
  }, [name, transport, url, command, registryKey])

  const probePayload = useMemo<McpProbeRequest>(() => {
    if (registryKey) {
      return { registry_key: registryKey, credential_id: credentialId }
    }
    return {
      transport,
      url: transport === 'stdio' ? null : url.trim(),
      command: transport === 'stdio' ? command.trim() : null,
      headers: kvToObject(headers),
      credential_id: credentialId,
    }
  }, [registryKey, credentialId, transport, url, command, headers])

  const probeKey = useMemo(() => JSON.stringify(probePayload), [probePayload])

  const selectedRegistryEntry = useMemo(() => {
    if (!registryKey) return null
    return (registry ?? []).find((entry) => entry.key === registryKey) ?? null
  }, [registry, registryKey])

  const usesMcpOAuth = credentialDefinitionFilter === 'mcp_oauth2'

  const oauthCredentialInitialData = useMemo(
    () => ({
      server_url: url.trim() || selectedRegistryEntry?.url || '',
      use_dynamic_client_registration: true,
      grant_type: 'pkce',
      authentication: 'none',
    }),
    [selectedRegistryEntry?.url, url],
  )

  const oauthCredentialInitialName = useMemo(() => {
    const base = name.trim() || selectedRegistryEntry?.display_name || 'Atlassian Rovo'
    return `${base} OAuth`
  }, [name, selectedRegistryEntry?.display_name])

  function resetProbePreview() {
    probeRequestSeqRef.current += 1
    lastSuccessfulProbeKeyRef.current = null
    inFlightProbeKeyRef.current = null
    setDiscoveredTools([])
    setProbeState({ kind: 'idle' })
  }

  function handlePickRegistryEntry(entry: McpRegistryEntry) {
    setRegistryKey(entry.key)
    setName(entry.display_name)
    setDescription(entry.description ?? '')
    setTransport(entry.transport)
    setUrl(entry.url ?? '')
    setCommand(entry.command ?? '')
    setArgs(entry.args ?? [])
    setEnvVars(
      Object.entries(entry.env_vars ?? {}).map(([key, value]) => ({
        key,
        value: String(value),
      })),
    )
    setHeaders([])
    setCredentialDefinitionFilter(entry.credential_definition_key)
    setCredentialId(null)
    resetProbePreview()
  }

  function clearRegistry() {
    setRegistryKey(null)
    setCredentialDefinitionFilter(null)
    setOauthPendingCredentialId(null)
    setOauthConnectedCredentialId(null)
    resetProbePreview()
  }

  useEffect(() => {
    function handleOAuthMessage(event: MessageEvent) {
      if (!isOAuthCompletedMessage(event.data)) return

      const allowedOrigins = new Set([window.location.origin])
      try {
        allowedOrigins.add(new URL(API_BASE).origin)
      } catch {
        // Keep the current-origin check when API_BASE is not a valid absolute URL.
      }
      if (!allowedOrigins.has(event.origin)) return

      const completedCredentialId = event.data.credentialId ?? null
      if (completedCredentialId && credentialId && completedCredentialId !== credentialId) return

      setOauthPendingCredentialId(null)
      setOauthConnectedCredentialId(completedCredentialId ?? credentialId)
      resetProbePreview()
      void queryClient.invalidateQueries({ queryKey: ['credentials'] })
      if (completedCredentialId) {
        void queryClient.invalidateQueries({ queryKey: ['credentials', completedCredentialId] })
      }
      toast.success(t('auth.oauthConnected'))
    }

    window.addEventListener('message', handleOAuthMessage)
    return () => window.removeEventListener('message', handleOAuthMessage)
  }, [credentialId, queryClient, t])

  async function handleOAuthConnect() {
    if (!credentialId) {
      toast.error(t('auth.oauthSelectFirst'))
      return
    }
    const popup = window.open('', 'moldy-mcp-oauth', 'popup,width=560,height=760')
    if (!popup) {
      toast.error(t('auth.oauthPopupBlocked'))
      return
    }
    try {
      popup.document.write('<!doctype html><title></title><body></body>')
      popup.document.close()
      popup.document.title = t('auth.oauthPopupTitle')
      if (popup.document.body) {
        popup.document.body.textContent = t('auth.oauthWaiting')
      }
    } catch {
      // The popup still stays open; navigation below is the important step.
    }
    try {
      const { authorization_url: authorizationUrl } = await startOAuth.mutateAsync(credentialId)
      popup.location.href = authorizationUrl
      setOauthPendingCredentialId(credentialId)
      setOauthConnectedCredentialId(null)
      popup.focus()
    } catch (e) {
      popup.close()
      toast.error(e instanceof Error ? e.message : t('auth.oauthStartFailed'))
    }
  }

  const runProbe = useCallback(async () => {
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
    probeRequestSeqRef.current += 1
    lastSuccessfulProbeKeyRef.current = null
    inFlightProbeKeyRef.current = null
    setDiscoveredTools([])
    setProbeState({ kind: 'idle' })
  }, [probeKey])

  // Auto-run probe on first Tools tab visit (when basics are valid). The ref
  // guards prevent duplicate calls under React strict-mode + tab flipping, but
  // still allow re-probe when the connection payload changes.
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

  function handleAddArg() {
    const trimmed = argDraft.trim()
    if (!trimmed) return
    // Allow space/comma-separated bulk add.
    const parts = trimmed.split(/[\s,]+/).filter(Boolean)
    setArgs((prev) => [...prev, ...parts])
    setArgDraft('')
  }

  async function handleSave() {
    if (!basicsValid) {
      toast.error(t('toast.required'))
      setTab('basics')
      return
    }
    try {
      const server = registryKey
        ? await createFromRegistry.mutateAsync({
            registry_key: registryKey,
            name: name.trim(),
            credential_id: credentialId,
          })
        : await create.mutateAsync({
            name: name.trim(),
            description: description.trim() || null,
            transport,
            url: transport === 'stdio' ? null : url.trim(),
            command: transport === 'stdio' ? command.trim() : null,
            args: transport === 'stdio' ? args : [],
            env_vars: transport === 'stdio' ? kvToObject(envVars) : {},
            headers: transport === 'stdio' ? {} : kvToObject(headers),
            credential_id: credentialId,
          })

      // Import the discovered tool rows.
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

  const saving = create.isPending || createFromRegistry.isPending || discover.isPending

  return (
    <>
      <DialogShell.Header
        icon={<DomainIconTile iconId="mcp" className="size-9" iconClassName="size-5" />}
        title={t('title')}
        description={t('description')}
        actions={<ProbeBadge state={probeState} />}
      />
      <DialogShell.Body>
        <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)}>
          <TabsList variant="line">
            <TabsTrigger value="basics">{t('tabs.basics')}</TabsTrigger>
            <TabsTrigger value="auth">{t('tabs.auth')}</TabsTrigger>
            <TabsTrigger value="tools">{t('tabs.tools')}</TabsTrigger>
          </TabsList>

          <TabsContent value="basics" className="pt-4">
            <BasicsTab
              registry={registry ?? []}
              registryKey={registryKey}
              onPickRegistry={handlePickRegistryEntry}
              onClearRegistry={clearRegistry}
              name={name}
              setName={setName}
              description={description}
              setDescription={setDescription}
              transport={transport}
              setTransport={(t) => {
                setTransport(t)
                resetProbePreview()
              }}
              url={url}
              setUrl={setUrl}
              command={command}
              setCommand={setCommand}
              args={args}
              setArgs={setArgs}
              argDraft={argDraft}
              setArgDraft={setArgDraft}
              onAddArg={handleAddArg}
              envVars={envVars}
              setEnvVars={setEnvVars}
              headers={headers}
              setHeaders={setHeaders}
            />
            <div className="mt-6 flex justify-end">
              <Button onClick={() => setTab('auth')} disabled={!basicsValid}>
                {t('actions.continueAuth')}
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="auth" className="pt-4">
            <AuthTab
              credentialId={credentialId}
              setCredentialId={setCredentialId}
              credentialDefinitionFilter={credentialDefinitionFilter}
              usesMcpOAuth={usesMcpOAuth}
              onCreateOAuthCredential={() => setCredentialCreateOpen(true)}
              onConnectOAuth={() => void handleOAuthConnect()}
              oauthStarting={startOAuth.isPending}
              oauthWaiting={oauthPendingCredentialId === credentialId}
              oauthConnected={oauthConnectedCredentialId === credentialId}
              probeState={probeState}
              onTest={runProbe}
              testing={probe.isPending}
            />
            <div className="mt-6 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setTab('basics')}>
                {t('actions.back')}
              </Button>
              <Button onClick={() => setTab('tools')}>{t('actions.continueTools')}</Button>
            </div>
          </TabsContent>

          <TabsContent value="tools" className="pt-4">
            <ToolsTab
              probeState={probeState}
              tools={discoveredTools}
              onRetry={() => void runProbe()}
            />
          </TabsContent>
        </Tabs>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose} disabled={saving}>
          {t('actions.cancel')}
        </Button>
        <Button onClick={handleSave} disabled={saving || !basicsValid}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {t('actions.save')}
        </Button>
      </DialogShell.Footer>
      {credentialCreateOpen ? (
        <CredentialCreateModal
          open={credentialCreateOpen}
          onOpenChange={setCredentialCreateOpen}
          presetDefinitionKey="mcp_oauth2"
          initialName={oauthCredentialInitialName}
          initialData={oauthCredentialInitialData}
          onCreated={(id) => {
            setCredentialId(id)
            setOauthConnectedCredentialId(null)
            setOauthPendingCredentialId(null)
          }}
        />
      ) : null}
    </>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// ProbeBadge — header status pill mirroring the live probe outcome.
// ──────────────────────────────────────────────────────────────────────────

function ProbeBadge({ state }: { state: ProbeState }) {
  const t = useTranslations('mcp.wizard.probe')
  if (state.kind === 'idle') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-status-warn/15 px-2 py-0.5 text-xs font-medium text-status-warn">
        {t('needed')}
      </span>
    )
  }
  if (state.kind === 'pending') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-status-info/15 px-2 py-0.5 text-xs font-medium text-status-info">
        <Loader2 className="size-3 animate-spin" />
        {t('pending')}
      </span>
    )
  }
  if (state.kind === 'ok') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-status-success/15 px-2 py-0.5 text-xs font-medium text-status-success">
        <CheckCircle2 className="size-3" />
        {t('ok', { count: state.toolCount })}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-status-danger/15 px-2 py-0.5 text-xs font-medium text-status-danger">
      <XCircle className="size-3" />
      {t('failed')}
    </span>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Basics tab — registry quick-start + manual transport-aware form.
// ──────────────────────────────────────────────────────────────────────────

interface BasicsTabProps {
  registry: McpRegistryEntry[]
  registryKey: string | null
  onPickRegistry: (entry: McpRegistryEntry) => void
  onClearRegistry: () => void
  name: string
  setName: (v: string) => void
  description: string
  setDescription: (v: string) => void
  transport: McpTransport
  setTransport: (v: McpTransport) => void
  url: string
  setUrl: (v: string) => void
  command: string
  setCommand: (v: string) => void
  args: string[]
  setArgs: React.Dispatch<React.SetStateAction<string[]>>
  argDraft: string
  setArgDraft: (v: string) => void
  onAddArg: () => void
  envVars: Array<{ key: string; value: string }>
  setEnvVars: React.Dispatch<React.SetStateAction<Array<{ key: string; value: string }>>>
  headers: Array<{ key: string; value: string }>
  setHeaders: React.Dispatch<React.SetStateAction<Array<{ key: string; value: string }>>>
}

function BasicsTab(props: BasicsTabProps) {
  return (
    <div className="space-y-6">
      <RegistrySection
        entries={props.registry}
        selected={props.registryKey}
        onSelect={props.onPickRegistry}
        onClear={props.onClearRegistry}
      />

      <ManualSection {...props} />
    </div>
  )
}

function RegistrySection({
  entries,
  selected,
  onSelect,
  onClear,
}: {
  entries: McpRegistryEntry[]
  selected: string | null
  onSelect: (entry: McpRegistryEntry) => void
  onClear: () => void
}) {
  const t = useTranslations('mcp.wizard.registry')
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t('title')}
        </h3>
        {selected ? (
          <Button size="sm" variant="ghost" onClick={onClear}>
            {t('clear')}
          </Button>
        ) : null}
      </div>
      {entries.length === 0 ? (
        <p className="rounded border border-dashed border-border/60 p-4 text-center text-xs text-muted-foreground">
          {t('empty')}
        </p>
      ) : (
        <div role="list" className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {entries.map((entry) => {
            const isSelected = selected === entry.key
            return (
              <button
                key={entry.key}
                type="button"
                role="listitem"
                onClick={() => onSelect(entry)}
                data-testid={`registry-card-${entry.key}`}
                className={`flex items-start gap-2.5 rounded-lg border p-2.5 text-left transition-[background-color,border-color,box-shadow] hover:bg-muted/50 ${
                  isSelected ? 'moldy-selected-card' : 'border-border'
                }`}
              >
                <DomainIconTile
                  iconId={entry.icon_id ?? 'server'}
                  className="size-9"
                  iconClassName="size-5"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{entry.display_name}</p>
                  {entry.description ? (
                    <p className="line-clamp-1 moldy-ui-caption text-muted-foreground">
                      {entry.description}
                    </p>
                  ) : null}
                  <p className="mt-1 moldy-ui-micro uppercase tracking-wide text-muted-foreground">
                    {entry.transport}
                  </p>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}

function ManualSection({
  registryKey,
  name,
  setName,
  description,
  setDescription,
  transport,
  setTransport,
  url,
  setUrl,
  command,
  setCommand,
  args,
  setArgs,
  argDraft,
  setArgDraft,
  onAddArg,
  envVars,
  setEnvVars,
  headers,
  setHeaders,
}: BasicsTabProps) {
  const t = useTranslations('mcp.wizard.manual')
  const isHttp = transport === 'sse' || transport === 'streamable_http'
  return (
    <section className="space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {registryKey ? t('override') : t('manual')}
      </h3>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label htmlFor="mcp-name">
            {t('name')} <span className="text-destructive">*</span>
          </label>
          <Input id="mcp-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="mcp-desc">{t('description')}</label>
          <Input
            id="mcp-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
      </div>

      {/* Transport radios */}
      <div className="space-y-1.5">
        <label>{t('transport')}</label>
        <div className="flex flex-wrap gap-2">
          {(['stdio', 'sse', 'streamable_http'] as const).map((option) => {
            const active = transport === option
            return (
              <button
                key={option}
                type="button"
                onClick={() => setTransport(option)}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                  active
                    ? 'border-primary-strong/60 bg-primary-strong/10 text-primary-strong'
                    : 'border-border hover:bg-muted/50'
                }`}
              >
                {t(`transportOptions.${option}`)}
              </button>
            )
          })}
        </div>
      </div>

      {/* Transport-specific fields */}
      {transport === 'stdio' ? (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="mcp-command">
              {t('command')} <span className="text-destructive">*</span>
            </label>
            <Input
              id="mcp-command"
              value={command}
              placeholder={t('commandPlaceholder')}
              onChange={(e) => setCommand(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <label>{t('args')}</label>
            <div className="flex flex-wrap gap-1.5">
              {args.map((arg, i) => (
                <span
                  key={`${arg}-${i}`}
                  className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 font-mono moldy-ui-caption"
                >
                  {arg}
                  <button
                    type="button"
                    aria-label={t('removeArg', { arg })}
                    onClick={() => setArgs((prev) => prev.filter((_, idx) => idx !== i))}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <Input
                value={argDraft}
                placeholder={t('argsPlaceholder')}
                onChange={(e) => setArgDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    onAddArg()
                  }
                }}
              />
              <Button type="button" variant="outline" onClick={onAddArg}>
                {t('add')}
              </Button>
            </div>
            <p className="moldy-ui-caption text-muted-foreground">{t('argsHint')}</p>
          </div>

          <KeyValueRows
            label={t('envVars')}
            rows={envVars}
            setRows={setEnvVars}
            keyPlaceholder="GITHUB_TOKEN"
            valuePlaceholder="{{ $credentials.token }}"
          />
        </div>
      ) : null}

      {isHttp ? (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="mcp-url">
              {t('url')} <span className="text-destructive">*</span>
            </label>
            <Input
              id="mcp-url"
              value={url}
              placeholder={t('urlPlaceholder')}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          <KeyValueRows
            label={t('headers')}
            rows={headers}
            setRows={setHeaders}
            keyPlaceholder="Authorization"
            valuePlaceholder="Bearer {{ $credentials.token }}"
          />
        </div>
      ) : null}
    </section>
  )
}

function KeyValueRows({
  label,
  rows,
  setRows,
  keyPlaceholder,
  valuePlaceholder,
}: {
  label: string
  rows: Array<{ key: string; value: string }>
  setRows: React.Dispatch<React.SetStateAction<Array<{ key: string; value: string }>>>
  keyPlaceholder?: string
  valuePlaceholder?: string
}) {
  const t = useTranslations('mcp.wizard.manual')
  function update(idx: number, patch: Partial<{ key: string; value: string }>) {
    setRows((prev) => prev.map((row, i) => (i === idx ? { ...row, ...patch } : row)))
  }
  function remove(idx: number) {
    setRows((prev) => prev.filter((_, i) => i !== idx))
  }
  function add() {
    setRows((prev) => [...prev, { key: '', value: '' }])
  }
  return (
    <div className="space-y-1.5">
      <label>{label}</label>
      <div className="space-y-1.5">
        {rows.map((row, i) => (
          <div key={i} className="flex gap-2">
            <Input
              value={row.key}
              placeholder={keyPlaceholder}
              onChange={(e) => update(i, { key: e.target.value })}
              className="font-mono text-xs"
            />
            <Input
              value={row.value}
              placeholder={valuePlaceholder}
              onChange={(e) => update(i, { value: e.target.value })}
              className="font-mono text-xs"
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => remove(i)}
              aria-label={t('removeRow')}
            >
              <X className="size-3.5" />
            </Button>
          </div>
        ))}
        <Button type="button" size="sm" variant="outline" onClick={add}>
          <Plus className="size-3.5" /> {t('addRow')}
        </Button>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Auth tab
// ──────────────────────────────────────────────────────────────────────────

function AuthTab({
  credentialId,
  setCredentialId,
  credentialDefinitionFilter,
  usesMcpOAuth,
  onCreateOAuthCredential,
  onConnectOAuth,
  oauthStarting,
  oauthWaiting,
  oauthConnected,
  probeState,
  onTest,
  testing,
}: {
  credentialId: string | null
  setCredentialId: (v: string | null) => void
  credentialDefinitionFilter: string | null
  usesMcpOAuth: boolean
  onCreateOAuthCredential: () => void
  onConnectOAuth: () => void
  oauthStarting: boolean
  oauthWaiting: boolean
  oauthConnected: boolean
  probeState: ProbeState
  onTest: () => void
  testing: boolean
}) {
  const t = useTranslations('mcp.wizard.auth')
  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <label>{t('credential')}</label>
        <CredentialPicker
          value={credentialId}
          onChange={setCredentialId}
          definitionKeys={credentialDefinitionFilter ? [credentialDefinitionFilter] : undefined}
        />
      </div>

      {usesMcpOAuth ? (
        <div className="rounded-md border border-border/60 bg-muted/30 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="outline" onClick={onCreateOAuthCredential}>
              <Plus className="size-3.5" />
              {t('createOAuthCredential')}
            </Button>
            <Button
              type="button"
              onClick={onConnectOAuth}
              disabled={!credentialId || oauthStarting}
            >
              {oauthStarting ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <KeyRound className="size-3.5" />
              )}
              {t('connectOAuth')}
            </Button>
          </div>
          {oauthConnected ? (
            <p className="mt-2 text-xs text-status-success">{t('oauthConnected')}</p>
          ) : null}
          {oauthWaiting ? (
            <p className="mt-2 text-xs text-muted-foreground">{t('oauthWaiting')}</p>
          ) : null}
        </div>
      ) : null}

      <div className="rounded-md border border-border/60 bg-muted/30 p-3 text-xs text-muted-foreground">
        <p className="font-medium text-foreground">{t('interpolation')}</p>
        <p className="mt-1">
          {t('interpolationBody')}{' '}
          <code className="rounded bg-background px-1 py-0.5 font-mono">
            {'{{ $credentials.<field> }}'}
          </code>
          . {t('interpolationSuffix')}
        </p>
        {credentialDefinitionFilter ? (
          <p className="mt-2">{t('filtered', { type: credentialDefinitionFilter })}</p>
        ) : null}
      </div>

      <div className="flex items-center gap-2">
        <Button onClick={onTest} disabled={testing} variant="outline">
          {testing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Activity className="size-3.5" />
          )}
          {t('test')}
        </Button>
        {probeState.kind === 'ok' ? (
          <span className="text-xs text-status-success">
            {t('connected', { count: probeState.toolCount })}
          </span>
        ) : null}
        {probeState.kind === 'error' ? (
          <span className="text-xs text-status-danger">{probeState.message}</span>
        ) : null}
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Tools tab — preview of tools that will be imported on save.
// ──────────────────────────────────────────────────────────────────────────

function ToolsTab({
  probeState,
  tools,
  onRetry,
}: {
  probeState: ProbeState
  tools: McpProbeTool[]
  onRetry: () => void
}) {
  const t = useTranslations('mcp.wizard.tools')
  if (probeState.kind === 'pending') {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> {t('discovering')}
      </div>
    )
  }
  if (probeState.kind === 'error') {
    return (
      <div className="space-y-3 rounded-md border border-status-danger/40 bg-status-danger/10 p-3 text-sm text-status-danger">
        <p className="font-medium">{t('probeFailed')}</p>
        <p className="text-xs">{probeState.message}</p>
        <Button size="sm" variant="outline" onClick={onRetry}>
          {t('retry')}
        </Button>
      </div>
    )
  }
  if (tools.length === 0) {
    return (
      <p className="rounded border border-dashed border-border/60 p-6 text-center text-xs text-muted-foreground">
        {t('empty')}
      </p>
    )
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{t('discovered', { total: tools.length })}</span>
        <Button size="sm" variant="ghost" onClick={onRetry}>
          {t('reprobe')}
        </Button>
      </div>
      <div className="space-y-1.5">
        {tools.map((tool) => {
          const paramCount = countParameters(tool.input_schema)
          return (
            <div
              key={tool.name}
              className="flex items-start gap-2.5 rounded-md border border-border/60 p-2.5"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-mono text-xs font-medium">{tool.name}</span>
                  <span className="rounded-full bg-muted px-1.5 py-0.5 moldy-ui-micro text-muted-foreground">
                    {t('params', { count: paramCount })}
                  </span>
                </div>
                {tool.description ? (
                  <p className="mt-0.5 line-clamp-2 moldy-ui-caption text-muted-foreground">
                    {tool.description}
                  </p>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function countParameters(schema: Record<string, unknown> | null | undefined): number {
  if (!schema || typeof schema !== 'object') return 0
  const props = (schema as Record<string, unknown>).properties
  if (!props || typeof props !== 'object') return 0
  return Object.keys(props as Record<string, unknown>).length
}

function kvToObject(rows: Array<{ key: string; value: string }>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const { key, value } of rows) {
    const k = key.trim()
    if (!k) continue
    out[k] = value
  }
  return out
}

function isOAuthCompletedMessage(data: unknown): data is {
  type: 'moldy.oauth.completed'
  credentialId?: string
} {
  if (!data || typeof data !== 'object') return false
  const maybe = data as Record<string, unknown>
  return maybe.type === 'moldy.oauth.completed'
}
