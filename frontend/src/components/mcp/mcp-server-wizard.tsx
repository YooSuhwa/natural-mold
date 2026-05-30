'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import {
  Activity,
  CheckCircle2,
  Loader2,
  Plus,
  Server,
  X,
  XCircle,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Checkbox } from '@/components/ui/checkbox'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { DomainIcon } from '@/components/shared/icon'
import { DialogShell } from '@/components/shared/dialog-shell'
import {
  useCreateFromRegistry,
  useCreateMcpServer,
  useDiscoverMcpTools,
  useMcpRegistry,
  useProbeMcpServer,
} from '@/lib/hooks/use-mcp-servers'
import type {
  McpProbeTool,
  McpRegistryEntry,
  McpTransport,
} from '@/lib/types/mcp'

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
  const { data: registry } = useMcpRegistry()
  const create = useCreateMcpServer()
  const createFromRegistry = useCreateFromRegistry()
  const discover = useDiscoverMcpTools()
  const probe = useProbeMcpServer()

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
  const [credentialDefinitionFilter, setCredentialDefinitionFilter] = useState<
    string | null
  >(null)

  // -- preview state -------------------------------------------------------
  const [discoveredTools, setDiscoveredTools] = useState<McpProbeTool[]>([])
  const [enabledNames, setEnabledNames] = useState<Set<string>>(new Set())
  const [probeState, setProbeState] = useState<ProbeState>({ kind: 'idle' })
  const probedRef = useRef(false)

  const basicsValid = useMemo(() => {
    if (!name.trim()) return false
    if (registryKey) return true
    if (transport === 'stdio') return command.trim().length > 0
    return url.trim().length > 0
  }, [name, transport, url, command, registryKey])

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
    // Force re-probe on next visit to Tools tab.
    probedRef.current = false
    setProbeState({ kind: 'idle' })
  }

  function clearRegistry() {
    setRegistryKey(null)
    setCredentialDefinitionFilter(null)
    probedRef.current = false
    setProbeState({ kind: 'idle' })
  }

  function buildProbePayload() {
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
  }

  async function runProbe() {
    if (!basicsValid) {
      toast.error(t('toast.required'))
      return
    }
    setProbeState({ kind: 'pending' })
    try {
      const result = await probe.mutateAsync(buildProbePayload())
      if (!result.success) {
        const msg = result.error ?? t('toast.probeFailed')
        setProbeState({ kind: 'error', message: msg })
        toast.error(msg)
        return
      }
      setDiscoveredTools(result.tools)
      // Default: enable everything we found.
      setEnabledNames(new Set(result.tools.map((t) => t.name)))
      setProbeState({ kind: 'ok', toolCount: result.tools.length })
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('toast.probeFailed')
      setProbeState({ kind: 'error', message: msg })
      toast.error(msg)
    }
  }

  // Auto-run probe on first Tools tab visit (when basics are valid). The ref
  // guard prevents duplicate calls under React strict-mode + tab flipping.
  useEffect(() => {
    if (tab !== 'tools') return
    if (!basicsValid) return
    if (probedRef.current) return
    if (probe.isPending) return
    probedRef.current = true
    void runProbe()
    // We intentionally exclude `runProbe` (recreated each render) — the ref
    // guard handles dedupe across renders within the same tab visit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, basicsValid])

  function toggleTool(name: string) {
    setEnabledNames((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

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
      const server =
        registryKey
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
      let discoveredCount = 0
      try {
        const result = await discover.mutateAsync(server.id)
        if (result.success) discoveredCount = result.tools.length
      } catch {
        toast.warning(t('toast.importFailedAfterSave'))
      }

      // Inform the user if they pre-selected fewer tools than discovered —
      // per-tool enable PATCH isn't surfaced via a wizard mutation yet, so
      // they should fine-tune from the detail page.
      if (discoveredCount > 0 && discoveredTools.length > 0) {
        const toDisableCount = discoveredTools.filter(
          (t) => !enabledNames.has(t.name),
        ).length
        if (toDisableCount > 0) {
          toast.info(t('toast.savedToggleLater', { count: toDisableCount }))
        }
      }

      toast.success(t('toast.added'))
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.addFailed'))
    }
  }

  const saving =
    create.isPending || createFromRegistry.isPending || discover.isPending

  return (
    <>
      <DialogShell.Header
        icon={<Server className="size-5" />}
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
                probedRef.current = false
                setProbeState({ kind: 'idle' })
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
              <Button
                onClick={() => setTab('auth')}
                disabled={!basicsValid}
              >
                {t('actions.continueAuth')}
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="auth" className="pt-4">
            <AuthTab
              credentialId={credentialId}
              setCredentialId={setCredentialId}
              credentialDefinitionFilter={credentialDefinitionFilter}
              probeState={probeState}
              onTest={runProbe}
              testing={probe.isPending}
            />
            <div className="mt-6 flex justify-end gap-2">
              <Button variant="outline" onClick={() => setTab('basics')}>
                {t('actions.back')}
              </Button>
              <Button onClick={() => setTab('tools')}>
                {t('actions.continueTools')}
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="tools" className="pt-4">
            <ToolsTab
              probeState={probeState}
              tools={discoveredTools}
              enabledNames={enabledNames}
              onToggle={toggleTool}
              onRetry={() => {
                probedRef.current = false
                void runProbe()
              }}
            />
          </TabsContent>
        </Tabs>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose} disabled={saving}>
          {t('actions.cancel')}
        </Button>
        <Button
          onClick={handleSave}
          disabled={saving || !basicsValid}
        >
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {t('actions.save')}
        </Button>
      </DialogShell.Footer>
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
  setEnvVars: React.Dispatch<
    React.SetStateAction<Array<{ key: string; value: string }>>
  >
  headers: Array<{ key: string; value: string }>
  setHeaders: React.Dispatch<
    React.SetStateAction<Array<{ key: string; value: string }>>
  >
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
        <div
          role="list"
          className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3"
        >
          {entries.map((entry) => {
            const isSelected = selected === entry.key
            return (
              <button
                key={entry.key}
                type="button"
                role="listitem"
                onClick={() => onSelect(entry)}
                data-testid={`registry-card-${entry.key}`}
                className={`flex items-start gap-2.5 rounded-lg border p-2.5 text-left transition-all hover:bg-muted/50 ${
                  isSelected
                    ? 'border-primary-strong/60 bg-primary-strong/10 shadow-sm'
                    : 'border-border'
                }`}
              >
                <DomainIcon
                  iconId={entry.icon_id ?? 'server'}
                  className="size-5"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {entry.display_name}
                  </p>
                  {entry.description ? (
                    <p className="line-clamp-1 text-[11px] text-muted-foreground">
                      {entry.description}
                    </p>
                  ) : null}
                  <p className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
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
          <Input
            id="mcp-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
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
                  className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 font-mono text-[11px]"
                >
                  {arg}
                  <button
                    type="button"
                    aria-label={t('removeArg', { arg })}
                    onClick={() =>
                      setArgs((prev) => prev.filter((_, idx) => idx !== i))
                    }
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
            <p className="text-[11px] text-muted-foreground">
              {t('argsHint')}
            </p>
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
  setRows: React.Dispatch<
    React.SetStateAction<Array<{ key: string; value: string }>>
  >
  keyPlaceholder?: string
  valuePlaceholder?: string
}) {
  const t = useTranslations('mcp.wizard.manual')
  function update(idx: number, patch: Partial<{ key: string; value: string }>) {
    setRows((prev) =>
      prev.map((row, i) => (i === idx ? { ...row, ...patch } : row)),
    )
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
  probeState,
  onTest,
  testing,
}: {
  credentialId: string | null
  setCredentialId: (v: string | null) => void
  credentialDefinitionFilter: string | null
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
          definitionKeys={
            credentialDefinitionFilter ? [credentialDefinitionFilter] : undefined
          }
        />
      </div>

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
          <p className="mt-2">
            {t('filtered', { type: credentialDefinitionFilter })}
          </p>
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
// Tools tab — preview + per-tool enable selection.
// ──────────────────────────────────────────────────────────────────────────

function ToolsTab({
  probeState,
  tools,
  enabledNames,
  onToggle,
  onRetry,
}: {
  probeState: ProbeState
  tools: McpProbeTool[]
  enabledNames: Set<string>
  onToggle: (name: string) => void
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
        <span>{t('enabled', { enabled: enabledNames.size, total: tools.length })}</span>
        <Button size="sm" variant="ghost" onClick={onRetry}>
          {t('reprobe')}
        </Button>
      </div>
      <div className="space-y-1.5">
        {tools.map((tool) => {
          const checked = enabledNames.has(tool.name)
          const paramCount = countParameters(tool.input_schema)
          return (
            <label
              key={tool.name}
              className="flex cursor-pointer items-start gap-2.5 rounded-md border border-border/60 p-2.5 transition-colors hover:bg-muted/40"
            >
              <Checkbox
                checked={checked}
                onCheckedChange={() => onToggle(tool.name)}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-mono text-xs font-medium">
                    {tool.name}
                  </span>
                  <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {t('params', { count: paramCount })}
                  </span>
                </div>
                {tool.description ? (
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
                    {tool.description}
                  </p>
                ) : null}
              </div>
            </label>
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
