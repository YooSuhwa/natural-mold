'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ArrowLeft, ArrowRight, Loader2, Check } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Separator } from '@/components/ui/separator'
import { CredentialPicker } from '@/components/credential/credential-picker'
import { DomainIcon } from '@/components/shared/icon'
import { McpToolTable } from './mcp-tool-table'
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

const STEPS = ['Basics', 'Auth', 'Tools'] as const
type Step = (typeof STEPS)[number]
type SourceTab = 'registry' | 'manual'

/**
 * 3-step MCP server wizard with two entry modes:
 *
 * - **From Registry**: pick a preset (GitHub/Linear/...) → auto-fills name,
 *   transport, URL/command, env vars and a credential filter — saves through
 *   `POST /api/mcp-servers/from-registry`.
 * - **Manual**: existing behaviour — free-form fields, saves via
 *   `POST /api/mcp-servers`.
 *
 * Step 3 (Tools) auto-runs discovery on entry so the user sees the imported
 * tool list immediately and can finalize with a single [Add] click.
 */
export function McpServerWizard({ open, onOpenChange }: McpServerWizardProps) {
  const { data: registry } = useMcpRegistry()

  const [tab, setTab] = useState<SourceTab>('registry')
  const [step, setStep] = useState<Step>('Basics')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [transport, setTransport] = useState<McpTransport>('streamable_http')
  const [url, setUrl] = useState('')
  const [command, setCommand] = useState('')
  const [credentialId, setCredentialId] = useState<string | null>(null)
  const [discoveredTools, setDiscoveredTools] = useState<McpProbeTool[]>([])
  const [registryKey, setRegistryKey] = useState<string | null>(null)
  const [credentialDefinitionFilter, setCredentialDefinitionFilter] = useState<
    string | null
  >(null)

  const create = useCreateMcpServer()
  const createFromRegistry = useCreateFromRegistry()
  const discover = useDiscoverMcpTools()
  const probe = useProbeMcpServer()
  const probedRef = useRef(false)

  function reset() {
    setTab('registry')
    setStep('Basics')
    setName('')
    setDescription('')
    setTransport('streamable_http')
    setUrl('')
    setCommand('')
    setCredentialId(null)
    setDiscoveredTools([])
    setRegistryKey(null)
    setCredentialDefinitionFilter(null)
    probedRef.current = false
  }

  function handleClose(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  const stepIndex = STEPS.indexOf(step)

  const basicsValid = useMemo(() => {
    if (!name.trim()) return false
    if (tab === 'registry') return registryKey !== null
    if (transport === 'stdio') return command.trim().length > 0
    return url.trim().length > 0
  }, [name, transport, url, command, tab, registryKey])

  function handlePickRegistryEntry(entry: McpRegistryEntry) {
    setRegistryKey(entry.key)
    setName(entry.display_name)
    setDescription(entry.description ?? '')
    setTransport(entry.transport)
    setUrl(entry.url ?? '')
    setCommand(entry.command ?? '')
    setCredentialDefinitionFilter(entry.credential_definition_key)
    setCredentialId(null)
  }

  async function handleNext() {
    if (step === 'Basics') {
      if (!basicsValid) {
        toast.error('Fill the required fields')
        return
      }
      setStep('Auth')
      return
    }

    if (step === 'Auth') {
      // No server INSERT here — wizard stays purely in-memory until [Add].
      // Step 3 will probe the live server to preview its tools without
      // touching the database.
      setStep('Tools')
      return
    }

    if (step === 'Tools') {
      // Final commit: now we actually create the server + import its tools.
      try {
        const server =
          tab === 'registry' && registryKey
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
                credential_id: credentialId,
              })
        // Trigger discovery so the imported mcp_tools rows are populated.
        // Soft-fail: if discover fails the server itself is still useful and
        // the user can retry from the detail sheet.
        try {
          await discover.mutateAsync(server.id)
        } catch {
          toast.warning(
            'Server added, but tool import failed. Retry from the detail page.',
          )
        }
        toast.success('MCP server added')
        handleClose(false)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Failed to add server')
      }
    }
  }

  function handleBack() {
    if (step === 'Auth') setStep('Basics')
    else if (step === 'Tools') setStep('Auth')
  }

  // Auto-probe (preview-only, no DB writes) when the user lands on Step 3.
  // The ref guard prevents duplicate calls from React strict-mode double-invoke
  // or from the user navigating Back → Next within the same modal session.
  useEffect(() => {
    if (step !== 'Tools') {
      if (step === 'Basics' || step === 'Auth') {
        probedRef.current = false
      }
      return
    }
    if (probedRef.current || probe.isPending) return
    probedRef.current = true
    void (async () => {
      try {
        const result = await probe.mutateAsync(
          tab === 'registry' && registryKey
            ? { registry_key: registryKey, credential_id: credentialId }
            : {
                transport,
                url: transport === 'stdio' ? null : url.trim(),
                command: transport === 'stdio' ? command.trim() : null,
                credential_id: credentialId,
              },
        )
        if (!result.success) {
          toast.error(result.error ?? 'Preview failed')
          probedRef.current = false
          return
        }
        setDiscoveredTools(result.tools)
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Preview failed')
        probedRef.current = false
      }
    })()
  }, [
    step,
    tab,
    registryKey,
    transport,
    url,
    command,
    credentialId,
    probe,
  ])

  // If user switches between tabs at Step 1, reset the in-flight values that
  // belong to the *other* tab so we don't ship a half-filled payload.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (step !== 'Basics') return
    setRegistryKey(null)
    setCredentialDefinitionFilter(null)
    setName('')
    setUrl('')
    setCommand('')
    setDescription('')
  }, [tab, step])
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New MCP server</DialogTitle>
          <DialogDescription>
            Step {stepIndex + 1} of {STEPS.length} — {step}
          </DialogDescription>
        </DialogHeader>

        {/* Stepper */}
        <ol className="flex items-center gap-2 text-xs">
          {STEPS.map((s, i) => (
            <li key={s} className="flex items-center gap-2">
              <span
                className={`flex size-5 items-center justify-center rounded-full ${
                  i < stepIndex
                    ? 'bg-emerald-500 text-white'
                    : i === stepIndex
                      ? 'bg-foreground text-background'
                      : 'bg-muted text-muted-foreground'
                }`}
              >
                {i < stepIndex ? <Check className="size-3" /> : i + 1}
              </span>
              <span
                className={
                  i === stepIndex
                    ? 'font-medium'
                    : 'text-muted-foreground'
                }
              >
                {s}
              </span>
              {i < STEPS.length - 1 && (
                <span className="text-muted-foreground">→</span>
              )}
            </li>
          ))}
        </ol>

        <Separator />

        <div className="min-h-[280px] min-w-0 max-h-[60vh] overflow-y-auto">
          {step === 'Basics' && (
            <Tabs
              value={tab}
              onValueChange={(v) => setTab(v as SourceTab)}
              className="w-full"
            >
              <TabsList className="w-full">
                <TabsTrigger value="registry">From Registry</TabsTrigger>
                <TabsTrigger value="manual">Manual</TabsTrigger>
              </TabsList>

              <TabsContent value="registry" className="pt-3">
                <RegistryGrid
                  entries={registry ?? []}
                  selected={registryKey}
                  onSelect={handlePickRegistryEntry}
                />
                {registryKey && (
                  <div className="mt-4 space-y-2">
                    <label
                      htmlFor="registry-name"
                      className="text-xs font-medium"
                    >
                      Name <span className="text-destructive">*</span>
                    </label>
                    <Input
                      id="registry-name"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                    <p className="text-[11px] text-muted-foreground">
                      Pre-filled from the catalog — rename if you want.
                    </p>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="manual" className="pt-3">
                <ManualBasicsForm
                  name={name}
                  setName={setName}
                  description={description}
                  setDescription={setDescription}
                  transport={transport}
                  setTransport={setTransport}
                  url={url}
                  setUrl={setUrl}
                  command={command}
                  setCommand={setCommand}
                />
              </TabsContent>
            </Tabs>
          )}

          {step === 'Auth' && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Optionally bind a credential. The MCP client will resolve{' '}
                <code className="rounded bg-muted px-1">
                  {'${credential.<field>}'}
                </code>{' '}
                templates in headers and env vars.
              </p>
              <CredentialPicker
                value={credentialId}
                onChange={setCredentialId}
                definitionKeys={
                  credentialDefinitionFilter
                    ? [credentialDefinitionFilter]
                    : undefined
                }
              />
              {credentialDefinitionFilter && (
                <p className="text-[11px] text-muted-foreground">
                  Filtered to credentials of type{' '}
                  <code className="rounded bg-muted px-1">
                    {credentialDefinitionFilter}
                  </code>
                  .
                </p>
              )}
            </div>
          )}

          {step === 'Tools' && (
            <div className="space-y-3">
              {probe.isPending ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Loading tools...
                </div>
              ) : discoveredTools.length > 0 ? (
                <>
                  <p className="text-sm text-muted-foreground">
                    Preview: {discoveredTools.length} tool
                    {discoveredTools.length === 1 ? '' : 's'} found. Click [Add]
                    to register the server.
                  </p>
                  <McpToolTable tools={discoveredTools} />
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No tools found. The server may be empty or unreachable.
                </p>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          {step !== 'Basics' && (
            <Button
              variant="outline"
              onClick={handleBack}
              disabled={
                probe.isPending ||
                create.isPending ||
                createFromRegistry.isPending ||
                discover.isPending
              }
            >
              <ArrowLeft className="size-4" /> Back
            </Button>
          )}
          <Button
            onClick={handleNext}
            disabled={
              (step === 'Basics' && !basicsValid) ||
              probe.isPending ||
              create.isPending ||
              createFromRegistry.isPending ||
              discover.isPending
            }
          >
            {(create.isPending ||
              createFromRegistry.isPending ||
              discover.isPending) && (
              <Loader2 className="size-4 animate-spin" />
            )}
            {step === 'Tools' ? 'Add' : 'Next'}
            {step !== 'Tools' && <ArrowRight className="size-4" />}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// -- Subcomponents ---------------------------------------------------------

function RegistryGrid({
  entries,
  selected,
  onSelect,
}: {
  entries: McpRegistryEntry[]
  selected: string | null
  onSelect: (entry: McpRegistryEntry) => void
}) {
  if (entries.length === 0) {
    return (
      <p className="rounded border border-dashed p-6 text-center text-xs text-muted-foreground">
        No registry entries available — switch to the Manual tab.
      </p>
    )
  }

  return (
    <div
      role="list"
      className="grid max-h-[300px] grid-cols-1 gap-2 overflow-auto sm:grid-cols-2"
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
            className={`flex items-start gap-3 rounded-lg border p-3 text-left transition-all hover:bg-muted/50 ${
              isSelected
                ? 'border-foreground/40 bg-muted shadow-sm'
                : 'border-border'
            }`}
          >
            <DomainIcon iconId={entry.icon_id ?? 'server'} className="size-5" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">
                {entry.display_name}
              </p>
              {entry.description && (
                <p className="line-clamp-2 text-[11px] text-muted-foreground">
                  {entry.description}
                </p>
              )}
              <p className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                {entry.transport}
              </p>
            </div>
          </button>
        )
      })}
    </div>
  )
}

function ManualBasicsForm({
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
}: {
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
}) {
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label htmlFor="mcp-name" className="text-xs font-medium">
          Name <span className="text-destructive">*</span>
        </label>
        <Input
          id="mcp-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <label htmlFor="mcp-desc" className="text-xs font-medium">
          Description
        </label>
        <Textarea
          id="mcp-desc"
          value={description}
          rows={2}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-xs font-medium">Transport</label>
        <Select
          value={transport}
          onValueChange={(v) => setTransport(v as McpTransport)}
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="streamable_http">Streamable HTTP</SelectItem>
            <SelectItem value="sse">SSE</SelectItem>
            <SelectItem value="stdio">stdio</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {transport === 'stdio' ? (
        <div className="space-y-1.5">
          <label htmlFor="mcp-command" className="text-xs font-medium">
            Command <span className="text-destructive">*</span>
          </label>
          <Input
            id="mcp-command"
            value={command}
            placeholder="/usr/local/bin/my-mcp-server"
            onChange={(e) => setCommand(e.target.value)}
          />
        </div>
      ) : (
        <div className="space-y-1.5">
          <label htmlFor="mcp-url" className="text-xs font-medium">
            URL <span className="text-destructive">*</span>
          </label>
          <Input
            id="mcp-url"
            value={url}
            placeholder="https://example.com/mcp"
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
      )}
    </div>
  )
}
